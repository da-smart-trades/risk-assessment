# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import json
from dataclasses import dataclass, field

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from cdk_nag import NagSuppressions
from constructs import Construct

# Litestar container port. The app-sg in NetworkStack already allows
# ALB ingress on this port — changing it requires updating that rule.
DEFAULT_CONTAINER_PORT = 8000

# Production listener serves real user traffic. Default action points at
# the blue target group on first deploy; CodeDeploy flips it to green
# during blue/green shifts (AppStack PR 2).
PRODUCTION_LISTENER_PORT = 443

# Test listener is internal-only (VPC CIDR ingress) — used by the
# BeforeAllowTraffic hook to probe the green target group before
# production traffic is shifted. Always points at green.
TEST_LISTENER_PORT = 8443

# Plaintext HTTP listener: returns a 301 redirect to the HTTPS port
# so visitors that hit http:// land on the encrypted listener
# without manually typing https://.
HTTP_REDIRECT_LISTENER_PORT = 80

# Defaults sized for the cert-ra Litestar workload (Granian + Inertia).
DEFAULT_CPU = 1024
DEFAULT_MEMORY_MIB = 2048
DEFAULT_DESIRED_COUNT = 2


def _empty_str_dict() -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class AppSecretInjection:
    """Maps an env var name to a Secrets Manager secret ARN, optionally
    with a JSON field extraction.

    The execution role is granted GetSecretValue on each ARN; the secret's
    encryption CMK gets KMS Decrypt via `secrets_cmk_arn` in the parent
    props (one grant covers all secrets since they share `cert-ra-secrets-cmk`).
    """

    env_var: str
    secret_arn: str
    field: str | None = None


@dataclass(frozen=True, slots=True)
class LitestarServiceProps:
    """Props for `LitestarService`.

    See § Blue/green deployment for AppStack — this construct provisions
    the Fargate service + ALB target groups + listener rules. The
    CodeDeploy application + deployment group land in a sibling
    construct (`BlueGreenDeployment`, AppStack PR 2).

    Cross-stack ARN-string pattern (rds_secret, ecr_repo, etc.): we
    import resources via `from_*_arn` factories so CDK doesn't try to
    mutate the source stack's resource policies, avoiding the dep
    cycle we hit on TemporalStack PR 4.
    """

    service_name: str
    """e.g. `cert-ra-app-staging` — Fargate service name."""

    vpc: ec2.IVpc
    private_subnets: list[ec2.ISubnet]

    app_security_group: ec2.ISecurityGroup
    """`cert-ra-app-sg` from NetworkStack. ALB -> app on container_port
    is already wired in NetworkStack."""

    alb: elbv2.IApplicationLoadBalancer
    """The public ALB shell from NetworkStack. We add listeners here."""

    alb_security_group: ec2.ISecurityGroup
    """`cert-ra-alb-sg` from NetworkStack. We add the :8443 test-listener
    ingress rule (VPC-CIDR only) here."""

    certificate_arn: str
    """ACM cert ARN for the public hostname, from DnsStack."""

    ecr_repo_arn: str
    """ARN of the `cert-ra` ECR repo (from IdentityStack). Imported via
    `from_repository_attributes` so we don't mutate the source stack."""

    ecr_repo_name: str
    """Just the repo name (e.g. `cert-ra`). Needed to construct the
    Repository.from_repository_attributes import alongside the ARN."""

    ecr_cmk_arn: str
    """`cert-ra-ecr-cmk` ARN. Execution role needs `kms:Decrypt` to pull
    encrypted image layers."""

    image_tag: str
    """Image tag to deploy. In CI this is `sha-<git_sha>` (immutable per
    ECR repo's IMMUTABLE tag policy). For initial-setup, operators pass
    `latest` or a known-good sha. CodeDeploy traffic shifts switch
    between task definition revisions, not between tags — once we land
    BlueGreenDeployment, the task def revision is the unit of deploy."""

    secrets_cmk_arn: str
    """`cert-ra-secrets-cmk` from SecretsStack. KMS Decrypt grant on the
    execution role covers all `secret_injections` at once."""

    secret_injections: list[AppSecretInjection]
    """Each entry mounts a Secrets Manager value as an ECS secret env var.
    Empty list disables app-secret injection (used in early bootstrap).
    The execution role is auto-granted GetSecretValue per ARN by the
    ECS L2."""

    rds_master_secret_arn: str
    """RDS credentials secret ARN. Same string-ARN pattern as the rest."""

    rds_master_secret_cmk_arn: str

    rds_endpoint: str
    """RDS hostname; container reads via `DATABASE_HOST` env."""

    rds_port: str

    logs_cmk_arn: str
    """`cert-ra-logs-cmk` ARN from ObservabilityStack for log group encryption."""

    app_url: str
    """Public-facing HTTPS URL (e.g. https://risk.example.com). Used in
    email links and MFA flows via AppSettings.url (CERT_RA_APP_URL)."""

    temporal_frontend_endpoint: str
    """`host:port` endpoint for the Temporal frontend (e.g.
    `internal-…elb.amazonaws.com:7233`). App container needs this to
    enqueue workflows (CERT_RA_TEMPORAL_HOST)."""

    temporal_tls_server_name: str
    """SNI / cert-CN the app validates against the Temporal frontend cert
    when speaking mTLS. Always `temporal-frontend.cert-ra.local` for
    cert-ra deploys (CERT_RA_TEMPORAL_TLS_SERVER_NAME)."""

    assets_bucket_name: str
    """`cert-ra-assets-{env}` S3 bucket name from DataStack. The app
    uploads avatars + security reports here (CERT_RA_STORAGE_BUCKET)."""

    assets_bucket_arn: str
    """ARN of the assets bucket. Used to grant the task role
    s3:GetObject/PutObject/DeleteObject + s3:ListBucket."""

    assets_s3_cmk_arn: str
    """`cert-ra-s3-cmk` ARN from DataStack — needed so the task role can
    encrypt/decrypt objects stored in the assets bucket."""

    aws_region: str
    """AWS region of the assets bucket (CERT_RA_STORAGE_AWS_REGION)."""

    operator_team_name: str
    """Display name of the operator team (CERT_RA_OPERATOR_TEAM_NAME)."""

    operator_team_domain: str
    """Email domain for operator team membership (CERT_RA_OPERATOR_TEAM_DOMAIN)."""

    operator_team_enforced_provider: str
    """OIDC provider operators must use, e.g. 'google'
    (CERT_RA_OPERATOR_TEAM_ENFORCED_PROVIDER)."""

    blocked_host_headers: tuple[str, ...] = ()
    """Hostnames the public listener must refuse with a fixed 421. Used
    during a domain migration: a third-party proxy (e.g. the old parent
    domain's DNS provider) may keep sending the ALB traffic for the OLD
    hostname with non-strict TLS, so swapping the cert and deleting the
    Route53 records is not enough to take the old name dark."""

    container_port: int = DEFAULT_CONTAINER_PORT
    cpu: int = DEFAULT_CPU
    memory_mib: int = DEFAULT_MEMORY_MIB
    desired_count: int = DEFAULT_DESIRED_COUNT
    log_retention: logs.RetentionDays = logs.RetentionDays.ONE_MONTH

    extra_env: dict[str, str] = field(default_factory=_empty_str_dict)
    """Plain (non-secret) env vars layered on top of the construct's defaults."""


class LitestarService(Construct):
    """Fargate service for the cert-ra Litestar app, fronted by the
    public ALB with blue + green target groups ready for blue/green.

    What this provisions:

    - ECS Fargate cluster (one per AppStack; reused by maint/migrations
      indirectly only via task families — they have their own clusters).
    - Task definition pulling from the `cert-ra` ECR repo at the
      configured `image_tag`.
    - ECS service with `deployment_controller=CODE_DEPLOY` — CDK
      registers task definition revisions but does NOT drive rollouts.
      CodeDeploy (AppStack PR 2) takes over from there.
    - **Two target groups** (`app-blue-tg`, `app-green-tg`) both targeting
      the same Fargate service. CodeDeploy shifts traffic between them
      by flipping listener default actions; CDK never sets attached
      target groups on the service itself (that's CodeDeploy's domain).
    - **Three listeners** on the ALB:
        - Production `:443` (HTTPS, ACM cert from DnsStack) — default
          action points at blue. Public-internet ingress.
        - HTTP redirect `:80` — returns a 301 to `https://<host>:443<path>`.
          Public-internet ingress; SG :80 rule added on the ALB SG here.
        - Test `:8443` — default action points at green. Internal-only
          (VPC CIDR ingress on the ALB SG) for the BeforeAllowTraffic
          hook to probe green before traffic is shifted.
    - Container env + secrets injection per `secret_injections`.

    What it does NOT do:
    - mTLS client cert injection for Temporal connections (worker side
      — handled by WorkersStack analogue).
    - Autoscaling targets / policies (handled by `WorkersStack` analogue).
    """

    cluster: ecs.Cluster
    service: ecs.FargateService
    task_definition: ecs.FargateTaskDefinition
    container: ecs.ContainerDefinition
    blue_target_group: elbv2.ApplicationTargetGroup
    green_target_group: elbv2.ApplicationTargetGroup
    production_listener: elbv2.ApplicationListener
    test_listener: elbv2.ApplicationListener
    http_redirect_listener: elbv2.ApplicationListener
    log_group: logs.LogGroup

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: LitestarServiceProps,
    ) -> None:
        super().__init__(scope, construct_id)
        self._props = props

        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name=f"{props.service_name}-cluster",
            vpc=props.vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        logs_cmk = kms.Key.from_key_arn(self, "LogsCmk", props.logs_cmk_arn)
        self.log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=f"/ecs/{props.service_name}",
            retention=props.log_retention,
            encryption_key=logs_cmk,
            # DESTROY (not RETAIN) so a CREATE rollback cleans the log
            # group up — RETAIN caused the `/ecs/cert-ra-app-prod already
            # exists` recovery friction on every redeploy after a failed
            # initial create. Logs from the surviving deploys are still
            # 30-day-retained in CloudWatch; this only affects what
            # happens to the *resource* when CFN drops the stack.
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        ecr_repo = ecr.Repository.from_repository_attributes(
            self,
            "EcrRepo",
            repository_arn=props.ecr_repo_arn,
            repository_name=props.ecr_repo_name,
        )

        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            cpu=props.cpu,
            memory_limit_mib=props.memory_mib,
            family=props.service_name,
            # Image is built for linux/arm64 (build.yml --platform linux/arm64).
            # Without this Fargate defaults to X86_64 and fails to pull.
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        rds_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "RdsSecret", props.rds_master_secret_arn
        )
        rds_cmk = kms.Key.from_key_arn(self, "RdsCmk", props.rds_master_secret_cmk_arn)

        # Derive the public hostname from `app_url` (e.g.
        # `https://risk.example.com` → `risk.example.com`). The CSRF
        # allowlist must include the apex and `www.` because AppStack
        # creates Route53 ARecords for both.
        public_host = (
            props.app_url.removeprefix("https://").removeprefix("http://").split("/")[0]
        )
        csrf_allowed_origins = json.dumps(
            [f"https://{public_host}", f"https://www.{public_host}"]
        )

        env: dict[str, str] = {
            "DATABASE_HOST": props.rds_endpoint,
            "DATABASE_PORT": props.rds_port,
            "LITESTAR_PORT": str(props.container_port),
            # AppSettings — public-facing URL used in email links and MFA.
            "CERT_RA_APP_URL": props.app_url,
            # HTTPS-only cookies in production. The app's pydantic
            # defaults are False so cookies work in local dev; we flip
            # them on here because the public listener is HTTPS-only
            # (port 80 just redirects to 443).
            "CERT_RA_APP_SESSION_COOKIE_SECURE": "true",
            "CERT_RA_APP_CSRF_COOKIE_SECURE": "true",
            # OriginCheckMiddleware fails closed on a localhost-only
            # default allowlist; explicitly authorize the env's public
            # hostname (apex + www) for state-changing requests.
            "CERT_RA_APP_CSRF_ALLOWED_ORIGINS": csrf_allowed_origins,
            # Temporal — app container needs the frontend address and
            # namespace if/when routes enqueue workflows directly. The
            # alerts-worker enable flag lives on the alerts WorkerService
            # (where the gate is actually read) — not here.
            "CERT_RA_TEMPORAL_HOST": props.temporal_frontend_endpoint,
            "CERT_RA_TEMPORAL_NAMESPACE": "default",
            # SNI used to validate the Temporal frontend cert. The
            # client cert/key/CA triplet itself is injected as ECS
            # secrets via `secret_injections`.
            "CERT_RA_TEMPORAL_TLS_SERVER_NAME": props.temporal_tls_server_name,
            # File storage — the app writes avatars + security reports
            # to the `cert-ra-assets-{env}` S3 bucket. Without these,
            # StorageSettings.backend defaults to "local" (uploads
            # disappear when the Fargate task is replaced).
            "CERT_RA_STORAGE_BACKEND": "s3",
            "CERT_RA_STORAGE_BUCKET": props.assets_bucket_name,
            "CERT_RA_STORAGE_AWS_REGION": props.aws_region,
            # Operator team — seeded once on first startup (idempotent).
            "CERT_RA_OPERATOR_TEAM_NAME": props.operator_team_name,
            "CERT_RA_OPERATOR_TEAM_DOMAIN": props.operator_team_domain,
            "CERT_RA_OPERATOR_TEAM_ENFORCED_PROVIDER": props.operator_team_enforced_provider,
            # Email — Resend in prod. API key is injected as a secret.
            "CERT_RA_EMAIL_ENABLED": "true",
            "CERT_RA_EMAIL_BACKEND": "resend",
            "CERT_RA_EMAIL_FROM_EMAIL": "noreply@certora.com",
        }
        env.update(props.extra_env)

        secrets: dict[str, ecs.Secret] = {
            "DATABASE_USER": ecs.Secret.from_secrets_manager(
                rds_secret, field="username"
            ),
            "DATABASE_PASSWORD": ecs.Secret.from_secrets_manager(
                rds_secret, field="password"
            ),
        }
        for injection in props.secret_injections:
            injected_secret = secretsmanager.Secret.from_secret_complete_arn(
                self,
                f"InjectedSecret{injection.env_var}",
                injection.secret_arn,
            )
            secrets[injection.env_var] = ecs.Secret.from_secrets_manager(
                injected_secret, field=injection.field
            )

        self.container = self.task_definition.add_container(
            "App",
            image=ecs.ContainerImage.from_ecr_repository(ecr_repo, props.image_tag),
            essential=True,
            environment=env,
            secrets=secrets,
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="app",
                log_group=self.log_group,
            ),
        )
        self.container.add_port_mappings(
            ecs.PortMapping(
                container_port=props.container_port,
                protocol=ecs.Protocol.TCP,
            ),
        )

        # KMS grants for imported CMKs. ECS L2 auto-grants on the
        # execution role's identity policy for secret reads, but
        # imported CMKs need explicit decrypt. Call obtain_execution_role
        # to materialise the role before grants attach.
        execution_role = self.task_definition.obtain_execution_role()
        rds_cmk.grant_decrypt(execution_role)
        if props.secret_injections:
            secrets_cmk = kms.Key.from_key_arn(
                self, "SecretsCmk", props.secrets_cmk_arn
            )
            secrets_cmk.grant_decrypt(execution_role)
        ecr_cmk = kms.Key.from_key_arn(self, "EcrCmk", props.ecr_cmk_arn)
        ecr_cmk.grant_decrypt(execution_role)

        # Assets bucket: the running container (task role, not execution
        # role) needs object + list permissions on `cert-ra-assets-{env}`.
        # We attach inline statements to keep this stack from mutating
        # DataStack's bucket policy (the bucket is owned there).
        task_role = self.task_definition.task_role
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AssetsBucketObjectRW",
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                ],
                resources=[f"{props.assets_bucket_arn}/*"],
            )
        )
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AssetsBucketList",
                effect=iam.Effect.ALLOW,
                actions=["s3:ListBucket", "s3:GetBucketLocation"],
                resources=[props.assets_bucket_arn],
            )
        )
        # The bucket is SSE-KMS encrypted with `cert-ra-s3-cmk`; the task
        # role needs encrypt + decrypt + GenerateDataKey to read and write
        # objects.
        s3_cmk = kms.Key.from_key_arn(self, "AssetsS3Cmk", props.assets_s3_cmk_arn)
        s3_cmk.grant_encrypt_decrypt(task_role)

        # Service. deployment_controller=CODE_DEPLOY tells ECS that
        # rollouts come from CodeDeploy, not from UpdateService calls
        # CDK might otherwise make on stack updates.
        self.service = ecs.FargateService(
            self,
            "Service",
            cluster=self.cluster,
            task_definition=self.task_definition,
            service_name=props.service_name,
            desired_count=props.desired_count,
            security_groups=[props.app_security_group],
            vpc_subnets=ec2.SubnetSelection(subnets=props.private_subnets),
            deployment_controller=ecs.DeploymentController(
                type=ecs.DeploymentControllerType.CODE_DEPLOY,
            ),
            enable_execute_command=True,
            assign_public_ip=False,
        )

        # Target groups. Both target the same Fargate service. ECS auto-
        # registers/deregisters tasks on the blue group on initial deploy;
        # green stays empty until CodeDeploy registers the next revision.
        self.blue_target_group = elbv2.ApplicationTargetGroup(
            self,
            "BlueTg",
            target_group_name=f"{props.service_name}-blue",
            vpc=props.vpc,
            port=props.container_port,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                # Public Inertia landing page — `exclude_from_auth=True`
                # so it serves 200 to anonymous probes. Earlier we used
                # `/health`, but that handler was decorated with
                # `@get("/health", ...)` and never added to
                # `get_route_handlers`, so it 404'd on every request and
                # the ALB marked every new ECS task unhealthy. Pointing
                # at `/landing/` is the operational workaround that
                # doesn't require a code rebuild; the proper /health
                # registration fix lives in src/cert_ra/api/domain/routes.py
                # but that needs an image rebuild + redeploy to land.
                path="/landing/",
                healthy_http_codes="200",
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
            ),
            deregistration_delay=cdk.Duration.seconds(30),
        )
        self.green_target_group = elbv2.ApplicationTargetGroup(
            self,
            "GreenTg",
            target_group_name=f"{props.service_name}-green",
            vpc=props.vpc,
            port=props.container_port,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                # Public Inertia landing page — `exclude_from_auth=True`
                # so it serves 200 to anonymous probes. Earlier we used
                # `/health`, but that handler was decorated with
                # `@get("/health", ...)` and never added to
                # `get_route_handlers`, so it 404'd on every request and
                # the ALB marked every new ECS task unhealthy. Pointing
                # at `/landing/` is the operational workaround that
                # doesn't require a code rebuild; the proper /health
                # registration fix lives in src/cert_ra/api/domain/routes.py
                # but that needs an image rebuild + redeploy to land.
                path="/landing/",
                healthy_http_codes="200",
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
            ),
            deregistration_delay=cdk.Duration.seconds(30),
        )
        # ECS service registers on blue initially. CodeDeploy moves tasks
        # to green during shifts; CDK doesn't track that drift.
        self.service.attach_to_application_target_group(self.blue_target_group)

        # Production listener — public HTTPS. Default action: blue.
        # We build the listeners with the L1-style constructor (passing
        # `load_balancer=...`) rather than `alb.add_listener(...)` because
        # the latter scopes the listener under the ALB's construct tree
        # (in NetworkStack), and the listener's reference to our local
        # target groups would create a cross-stack cycle:
        #   NetworkStack(listener) -> AppStack(TG) and
        #   AppStack(service) -> NetworkStack(listener).
        # By making the listener a child of *this* construct (in AppStack)
        # we keep both edges inside AppStack.
        certificate = elbv2.ListenerCertificate.from_arn(props.certificate_arn)
        self.production_listener = elbv2.ApplicationListener(
            self,
            "ProductionListener",
            load_balancer=props.alb,
            port=PRODUCTION_LISTENER_PORT,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            certificates=[certificate],
            default_target_groups=[self.blue_target_group],
            ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS,
        )

        # Test listener — internal-only HTTPS on :8443; default action: green.
        # Reuse the same ACM cert (the test hook validates against it).
        # Ingress for :8443 is added below on the ALB security group.
        self.test_listener = elbv2.ApplicationListener(
            self,
            "TestListener",
            load_balancer=props.alb,
            port=TEST_LISTENER_PORT,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            certificates=[certificate],
            default_target_groups=[self.green_target_group],
            ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS,
        )

        # Refuse decommissioned hostnames before they reach the app. The
        # rule (not the default action) carries the block so CodeDeploy
        # can keep swapping the default forward action between blue and
        # green untouched. 421 Misdirected Request is the status defined
        # for "this server is not configured for that authority".
        if props.blocked_host_headers:
            for listener_id, listener in (
                ("Production", self.production_listener),
                ("Test", self.test_listener),
            ):
                elbv2.ApplicationListenerRule(
                    self,
                    f"BlockedHosts{listener_id}",
                    listener=listener,
                    priority=5,
                    conditions=[
                        elbv2.ListenerCondition.host_headers(
                            list(props.blocked_host_headers)
                        )
                    ],
                    action=elbv2.ListenerAction.fixed_response(
                        421,
                        content_type="text/plain",
                        message_body="This hostname is no longer served.",
                    ),
                )

        props.alb_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(props.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(TEST_LISTENER_PORT),
            description="BeforeAllowTraffic hook to ALB test listener",
        )

        # HTTP redirect listener — turns http://host/path?query into
        # https://host:443/path?query so we never serve plaintext.
        # No target group; the default action is a fixed-response 301.
        self.http_redirect_listener = elbv2.ApplicationListener(
            self,
            "HttpRedirectListener",
            load_balancer=props.alb,
            port=HTTP_REDIRECT_LISTENER_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_action=elbv2.ListenerAction.redirect(
                protocol="HTTPS",
                port=str(PRODUCTION_LISTENER_PORT),
                # `#{host}` / `#{path}` / `#{query}` are ALB-side
                # template tokens, not Python f-string placeholders.
                host="#{host}",
                path="/#{path}",
                query="#{query}",
                permanent=True,
            ),
        )
        NagSuppressions.add_resource_suppressions(
            self.http_redirect_listener,
            [
                {
                    "id": "NIST.800.53.R5-ELBv2ACMCertificateRequired",
                    "reason": (
                        "Redirect-only listener — never terminates TLS and "
                        "never forwards plaintext to a target. Its single "
                        "default action is a 301 to the HTTPS listener (which "
                        "DOES use an ACM cert). Requiring a cert here would "
                        "be a no-op."
                    ),
                },
                {
                    "id": "AwsSolutions-ELB1",
                    "reason": (
                        "Same as NIST.800.53.R5-ELBv2ACMCertificateRequired: "
                        "redirect-only listener has no protocol upgrade target."
                    ),
                },
            ],
        )
        # Public-internet ingress for :80. The redirect listener never
        # forwards plaintext to the app; the rule just lets browsers
        # arrive on :80 so the ALB can hand back the 301.
        props.alb_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(HTTP_REDIRECT_LISTENER_PORT),
            description="Public HTTP to HTTPS redirect",
        )

        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                    "reason": (
                        "ECS L2 TaskDefinition auto-creates the execution role "
                        "with an inline policy for secrets + KMS + CW Logs + ECR. "
                        "We don't author this policy directly."
                    ),
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "CW Logs writes use wildcards on log-stream name; "
                        "ECR pulls use wildcards on layer digests. Neither is "
                        "predictable at deploy time."
                    ),
                },
                {
                    "id": "AwsSolutions-ECS2",
                    "reason": (
                        "Non-secret env vars (DATABASE_HOST, LITESTAR_PORT, "
                        "etc.) are the connection metadata the container needs "
                        "before secrets can be resolved. All credentials are "
                        "injected via ECS Secrets."
                    ),
                },
                {
                    "id": "AwsSolutions-ECS4",
                    "reason": (
                        "Container Insights v2 is enabled on the cluster "
                        "(ContainerInsights.ENABLED), satisfying this control. "
                        "Suppression handles cdk-nag's earlier-tier check that "
                        "looks for the v1 attribute."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @property
    def task_role(self) -> iam.IRole:
        """The container's runtime IAM role (NOT the execution role).
        Consumers extend it (e.g. for additional Secrets Manager reads
        outside the construct's `secret_injections` list)."""
        return self.task_definition.task_role
