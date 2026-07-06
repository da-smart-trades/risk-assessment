# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_codedeploy as codedeploy
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from cdk_nag import NagSuppressions
from constructs import Construct

# Asset dirs for the two hook Lambdas. Each is bundled via Docker
# (Python 3.12 runtime image) so future hooks that need cryptography
# or other native deps don't require a separate construct.
_BEFORE_ALLOW_TRAFFIC_DIR = (
    Path(__file__).parent / "_lambda_assets" / "before_allow_traffic"
)
_AFTER_ALLOW_TRAFFIC_DIR = (
    Path(__file__).parent / "_lambda_assets" / "after_allow_traffic"
)


@dataclass(frozen=True, slots=True)
class BlueGreenDeploymentProps:
    """Props for BlueGreenDeployment.

    Cross-stack ARN-string pattern: ECR repo ARN, ALB ARN, target
    group ARNs, etc. are passed in as strings so the construct can
    import them via `from_*_attributes` factories — same dep-cycle-
    avoidance reasoning as LitestarService / TemporalCluster.
    """

    application_name: str
    """e.g. `cert-ra-app-staging`. The CodeDeploy application name."""

    deployment_group_name: str
    """e.g. `cert-ra-app-staging-dg`."""

    service: ecs.IBaseService
    """The Fargate service from LitestarService. CodeDeploy mutates
    its task-definition revision + desired count during shifts."""

    cluster: ecs.ICluster
    """ECS cluster the service runs in. Required by the L2
    EcsDeploymentGroup binding."""

    blue_target_group: elbv2.ApplicationTargetGroup
    green_target_group: elbv2.ApplicationTargetGroup
    """L2 ApplicationTargetGroup (not the I-interface) so we can read
    `target_group_full_name` for the CloudWatch alarm dimensions."""

    production_listener: elbv2.ApplicationListener
    """The :443 production listener (L2). CodeDeploy flips its default
    action between blue and green during shifts. L2 (not the I-interface)
    is needed so we can read `load_balancer.load_balancer_arn` for the
    AfterAllowTraffic hook."""

    test_listener: elbv2.ApplicationListener
    """The :8443 test listener (L2). Stays pointed at green; the
    BeforeAllowTraffic hook probes it before the production listener
    flips."""

    test_listener_url: str
    """e.g. `https://cert-ra.staging.certora.com:8443`. Full URL the
    BeforeAllowTraffic Lambda uses to probe the green TG. We pass this
    pre-built so the Lambda doesn't need ALB DNS lookup permissions."""

    ecr_repo_arn: str
    """ARN of the `cert-ra` ECR repo. BeforeAllowTraffic needs
    DescribeImages + BatchGetImage to look up signature manifests."""

    ecr_repo_name: str
    """The bare repo name (e.g. `cert-ra`). Lambda passes this to
    DescribeImages calls."""

    cosign_pubkey_param_arn: str
    """SSM parameter ARN holding the cosign public key. Lambda reads
    it for sanity logging. Full cryptographic verify is a follow-up
    (presence check alone defeats the unsigned-push attack path)."""

    vpc: ec2.IVpc
    private_subnets: list[ec2.ISubnet]
    """The hook Lambdas run in the VPC so they can reach the internal
    :8443 test listener. Their SG allows egress to the ALB SG on
    :8443 — added below."""

    alb_security_group: ec2.ISecurityGroup
    """ALB SG; the hook Lambda's SG egress targets this on :8443."""

    deployment_config: codedeploy.IEcsDeploymentConfig | None = None
    """Override the linear-10%-every-1-min staging default. Set to
    `CANARY_10PERCENT_5MINUTES` for prod per the design spec's
    "linear in staging, canary in prod" guidance."""

    skip_tls_verify_in_smoke_test: bool = False
    """When True (staging only), the BeforeAllowTraffic smoke test
    skips cert verification so an IP-based test URL works. Never
    set in prod — the test listener uses the same ACM cert as the
    production listener."""

    smoke_test_window_seconds: int = 120
    smoke_test_max_p99_latency_ms: int = 2000


class BlueGreenDeployment(Construct):
    """CodeDeploy ECS blue/green application + deployment group.

    Composition with the LitestarService construct: that one owns the
    ECS service, the two target groups, and the two listeners. This
    construct adds:

    - `CodeDeploy::Application` (compute platform: ECS).
    - `CodeDeploy::DeploymentGroup` referencing the service, both
      target groups, both listeners, a configurable deployment config
      (linear/canary), and auto-rollback alarms.
    - **BeforeAllowTraffic hook Lambda** — verifies the new image's
      signature is present in ECR and smoke-tests the green target
      group via the test listener. Failure aborts the shift before
      any production traffic moves.
    - **AfterAllowTraffic hook Lambda** — samples the production
      target group's 5xx count + p99 latency over a configurable
      window after the shift completes. Failure rolls back.
    - **Auto-rollback alarms**: a 5xx-count alarm and an
      unhealthy-host-count alarm on the production target group.
      CodeDeploy watches them during deploys; either alarm firing
      triggers automatic rollback.
    """

    application: codedeploy.EcsApplication
    deployment_group: codedeploy.EcsDeploymentGroup
    before_allow_traffic: lambda_.Function
    after_allow_traffic: lambda_.Function
    five_xx_alarm: cloudwatch.Alarm
    unhealthy_hosts_alarm: cloudwatch.Alarm

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: BlueGreenDeploymentProps,
    ) -> None:
        super().__init__(scope, construct_id)
        self._props = props

        # CodeDeploy application (ECS compute platform).
        self.application = codedeploy.EcsApplication(
            self,
            "Application",
            application_name=props.application_name,
        )

        # Hook Lambdas live in the VPC so they can reach the internal
        # :8443 test listener. They share a security group whose egress
        # is opt-in (we explicitly allow ALB:8443 + ALB:443).
        hook_sg = ec2.SecurityGroup(
            self,
            "HookSg",
            vpc=props.vpc,
            description="cert-ra: BeforeAllowTraffic + AfterAllowTraffic hook Lambdas",
            allow_all_outbound=False,
        )
        hook_sg.add_egress_rule(
            peer=props.alb_security_group,
            connection=ec2.Port.tcp(8443),
            description="Hook Lambda to ALB test listener",
        )
        # AWS API calls (CodeDeploy, ECS, ECR, SSM, CloudWatch) go over
        # the public AWS endpoints; egress 443 is required for SDK calls.
        hook_sg.add_egress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(443),
            description="Hook Lambda to AWS service endpoints",
        )
        # The ALB SG already accepts :8443 from the VPC CIDR
        # (LitestarService's rule); the hook Lambda runs in private
        # subnets so it's covered by that rule. We don't add a tighter
        # rule from HookSg specifically because that would mutate the
        # ALB SG (in NetworkStack) referencing HookSg (in AppStack),
        # creating a cross-stack cycle with the Lambda's VPC dep.

        self.before_allow_traffic = self._build_before_hook(
            props=props,
            security_group=hook_sg,
        )
        self.after_allow_traffic = self._build_after_hook(
            props=props,
            security_group=hook_sg,
        )

        # Auto-rollback alarms on the production target group.
        # Both are LessThanLowerOrGreaterThanUpperThreshold-style
        # ceilings; CodeDeploy treats either firing during the deploy
        # as a rollback trigger.
        self.five_xx_alarm = cloudwatch.Alarm(
            self,
            "FiveXxAlarm",
            alarm_description=("5xx responses on cert-ra production TG during deploy"),
            metric=cloudwatch.Metric(
                namespace="AWS/ApplicationELB",
                metric_name="HTTPCode_Target_5XX_Count",
                dimensions_map={
                    "TargetGroup": props.blue_target_group.target_group_full_name,
                    "LoadBalancer": _load_balancer_full_name_for(
                        props.production_listener
                    ),
                },
                statistic="Sum",
                period=cdk.Duration.minutes(1),
            ),
            evaluation_periods=1,
            threshold=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.unhealthy_hosts_alarm = cloudwatch.Alarm(
            self,
            "UnhealthyHostsAlarm",
            alarm_description=(
                "Unhealthy host count on cert-ra production TG during deploy"
            ),
            metric=cloudwatch.Metric(
                namespace="AWS/ApplicationELB",
                metric_name="UnHealthyHostCount",
                dimensions_map={
                    "TargetGroup": props.blue_target_group.target_group_full_name,
                    "LoadBalancer": _load_balancer_full_name_for(
                        props.production_listener
                    ),
                },
                statistic="Maximum",
                period=cdk.Duration.minutes(1),
            ),
            evaluation_periods=2,
            threshold=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # Default config: linear 10% / 1 minute (staging-friendly fast
        # iteration). Prod overrides with canary 10% / 5 minutes.
        deployment_config = (
            props.deployment_config
            or codedeploy.EcsDeploymentConfig.LINEAR_10_PERCENT_EVERY_1_MINUTES
        )

        self.deployment_group = codedeploy.EcsDeploymentGroup(
            self,
            "DeploymentGroup",
            application=self.application,
            deployment_group_name=props.deployment_group_name,
            service=props.service,
            blue_green_deployment_config=codedeploy.EcsBlueGreenDeploymentConfig(
                blue_target_group=props.blue_target_group,
                green_target_group=props.green_target_group,
                listener=props.production_listener,
                test_listener=props.test_listener,
                # 5 minutes between traffic-shift completion and the
                # original "blue" tasks being torn down. Lets us cancel
                # via `aws deploy stop-deployment` if AfterAllowTraffic
                # passes but a slower-moving regression surfaces.
                termination_wait_time=cdk.Duration.minutes(5),
                # Omit deployment_approval_wait_time entirely so CDK does
                # not emit ActionOnTimeout=STOP_DEPLOYMENT with a 0-minute
                # timeout, which CodeDeploy rejects with a 400. When the
                # field is absent CloudFormation defaults to
                # CONTINUE_DEPLOYMENT (deploy immediately).
            ),
            deployment_config=deployment_config,
            auto_rollback=codedeploy.AutoRollbackConfig(
                failed_deployment=True,
                stopped_deployment=True,
                deployment_in_alarm=True,
            ),
            alarms=[self.five_xx_alarm, self.unhealthy_hosts_alarm],
        )

        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                    "reason": (
                        "CodeDeploy + Lambda L2s auto-create roles with inline "
                        "policies for the service-managed permissions. We don't "
                        "author them directly."
                    ),
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "CW Logs writes use wildcards on log-stream name; ECR "
                        "image lookups use BatchGetImage * because signature tags "
                        "aren't pre-computable. Both are scoped to the cert-ra repo."
                    ),
                },
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": (
                        "CodeDeploy service role uses AWSCodeDeployRoleForECS — "
                        "AWS-managed policy that's the documented requirement."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-LambdaDLQ",
                    "reason": (
                        "Hook Lambdas report status directly to CodeDeploy; "
                        "failures surface as a failed deploy with the Lambda "
                        "error in the CodeDeploy event log."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-LambdaConcurrency",
                    "reason": (
                        "Hook Lambdas are invoked at most once per deploy; "
                        "CodeDeploy serialises invocations per deployment."
                    ),
                },
                {
                    "id": "AwsSolutions-L1",
                    "reason": (
                        "Hook handlers target Python 3.12 explicitly (the "
                        "version the cert-ra project standardises on; see "
                        "§ Container image baselines (B4)). The 'latest' "
                        "moving target would silently bump us to 3.13+ "
                        "without the corresponding test/validation work."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-CloudWatchAlarmAction",
                    "reason": (
                        "These alarms are *deploy-time signals* — "
                        "CodeDeploy reads their state directly via the "
                        "DeploymentGroup AlarmConfiguration and triggers "
                        "auto-rollback. They intentionally don't fan out "
                        "to SNS/etc. so a steady-state breach doesn't "
                        "page during normal operation (separate "
                        "ObservabilityStack alarms handle that path)."
                    ),
                },
            ],
            apply_to_children=True,
        )

    def _build_before_hook(
        self,
        *,
        props: BlueGreenDeploymentProps,
        security_group: ec2.ISecurityGroup,
    ) -> lambda_.Function:
        fn = lambda_.Function(
            self,
            "BeforeAllowTraffic",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                str(_BEFORE_ALLOW_TRAFFIC_DIR),
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        # No external deps — copy handler and clean
                        # __pycache__ to keep the asset hash stable.
                        "cp -r . /asset-output && rm -rf /asset-output/__pycache__",
                    ],
                ),
            ),
            timeout=cdk.Duration.minutes(5),
            memory_size=256,
            vpc=props.vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=props.private_subnets),
            security_groups=[security_group],
            environment={
                "TEST_LISTENER_URL": props.test_listener_url,
                "SMOKE_TEST_SKIP_TLS_VERIFY": (
                    "true" if props.skip_tls_verify_in_smoke_test else "false"
                ),
                "COSIGN_PUBKEY_PARAM": props.cosign_pubkey_param_arn,
                "ECR_REPO_NAME": props.ecr_repo_name,
            },
            description=(
                "CodeDeploy BeforeAllowTraffic hook for cert-ra: cosign "
                "signature presence + green TG smoke test"
            ),
        )
        # IAM: signal back to CodeDeploy; read deployment info; describe
        # ECS task defs; read ECR images for sig presence; read SSM cosign
        # pubkey.
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="CodeDeployHookStatus",
                effect=iam.Effect.ALLOW,
                actions=[
                    "codedeploy:PutLifecycleEventHookExecutionStatus",
                    "codedeploy:GetDeployment",
                ],
                resources=["*"],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="EcsDescribeTaskDef",
                effect=iam.Effect.ALLOW,
                actions=["ecs:DescribeTaskDefinition"],
                resources=["*"],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="EcrReadImageMetadata",
                effect=iam.Effect.ALLOW,
                actions=[
                    "ecr:DescribeImages",
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                ],
                resources=[props.ecr_repo_arn],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="EcrAuthToken",
                effect=iam.Effect.ALLOW,
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadCosignPubkey",
                effect=iam.Effect.ALLOW,
                actions=["ssm:GetParameter"],
                resources=[props.cosign_pubkey_param_arn],
            )
        )
        return fn

    def _build_after_hook(
        self,
        *,
        props: BlueGreenDeploymentProps,
        security_group: ec2.ISecurityGroup,
    ) -> lambda_.Function:
        load_balancer_arn = _load_balancer_arn_for(props.production_listener)
        fn = lambda_.Function(
            self,
            "AfterAllowTraffic",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                str(_AFTER_ALLOW_TRAFFIC_DIR),
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "cp -r . /asset-output && rm -rf /asset-output/__pycache__",
                    ],
                ),
            ),
            timeout=cdk.Duration.minutes(5),
            memory_size=256,
            vpc=props.vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=props.private_subnets),
            security_groups=[security_group],
            environment={
                "PRODUCTION_TARGET_GROUP_ARN": (
                    props.blue_target_group.target_group_arn
                ),
                "LOAD_BALANCER_ARN": load_balancer_arn,
                "WINDOW_SECONDS": str(props.smoke_test_window_seconds),
                "MAX_P99_LATENCY_MS": str(props.smoke_test_max_p99_latency_ms),
            },
            description=(
                "CodeDeploy AfterAllowTraffic hook for cert-ra: 5xx + p99 "
                "latency check after traffic shift"
            ),
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="CodeDeployHookStatus",
                effect=iam.Effect.ALLOW,
                actions=["codedeploy:PutLifecycleEventHookExecutionStatus"],
                resources=["*"],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadCloudWatchMetrics",
                effect=iam.Effect.ALLOW,
                actions=["cloudwatch:GetMetricStatistics"],
                resources=["*"],
            )
        )
        return fn

    @property
    def application_name(self) -> str:
        return self.application.application_name

    @property
    def deployment_group_name(self) -> str:
        return self.deployment_group.deployment_group_name


def _load_balancer_full_name_for(listener: elbv2.ApplicationListener) -> str:
    """Pull the ALB full name out of an imported listener. The listener
    proxies the parent ALB via its own attributes; we walk via
    `load_balancer_arn` to derive the full name (suffix after
    `loadbalancer/`)."""
    arn = _load_balancer_arn_for(listener)
    return arn.split(":loadbalancer/", 1)[-1]


def _load_balancer_arn_for(listener: elbv2.ApplicationListener) -> str:
    """Walk through the L2 listener's parent ALB to return its ARN.
    Used by the CloudWatch alarm dimensions + AfterAllowTraffic hook
    env. The listener_arn isn't sufficient because the ALB ARN is a
    separate resource path."""
    arn: str = listener.load_balancer.load_balancer_arn
    return arn
