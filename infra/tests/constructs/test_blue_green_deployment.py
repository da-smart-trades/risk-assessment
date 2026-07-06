# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_codedeploy as codedeploy
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2

from cert_ra_infra.constructs.app.blue_green_deployment import (
    BlueGreenDeployment,
    BlueGreenDeploymentProps,
)

_ECR_REPO_ARN = "arn:aws:ecr:us-east-1:111111111111:repository/cert-ra"
_COSIGN_PARAM_ARN = (
    "arn:aws:ssm:us-east-1:111111111111:parameter/cert-ra/signing/cosign-pubkey"
)
_TEST_LISTENER_URL = "https://cert-ra.staging.certora.com:8443"


def _synth(
    *,
    deployment_config: codedeploy.IEcsDeploymentConfig | None = None,
    skip_tls_verify: bool = False,
) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    vpc = ec2.Vpc(stack, "Vpc", max_azs=2, nat_gateways=0)
    cluster = ecs.Cluster(stack, "Cluster", vpc=vpc)
    task_def = ecs.FargateTaskDefinition(
        stack, "TaskDef", cpu=256, memory_limit_mib=512
    )
    task_def.add_container(
        "App",
        image=ecs.ContainerImage.from_registry("nginx:latest"),
        port_mappings=[ecs.PortMapping(container_port=8000)],
    )
    service = ecs.FargateService(
        stack,
        "Service",
        cluster=cluster,
        task_definition=task_def,
        deployment_controller=ecs.DeploymentController(
            type=ecs.DeploymentControllerType.CODE_DEPLOY,
        ),
    )
    alb_sg = ec2.SecurityGroup(stack, "AlbSg", vpc=vpc, allow_all_outbound=True)
    alb = elbv2.ApplicationLoadBalancer(
        stack, "Alb", vpc=vpc, internet_facing=True, security_group=alb_sg
    )
    blue = elbv2.ApplicationTargetGroup(
        stack,
        "Blue",
        vpc=vpc,
        port=8000,
        protocol=elbv2.ApplicationProtocol.HTTP,
        target_type=elbv2.TargetType.IP,
    )
    green = elbv2.ApplicationTargetGroup(
        stack,
        "Green",
        vpc=vpc,
        port=8000,
        protocol=elbv2.ApplicationProtocol.HTTP,
        target_type=elbv2.TargetType.IP,
    )
    production_listener = elbv2.ApplicationListener(
        stack,
        "Prod",
        load_balancer=alb,
        port=443,
        protocol=elbv2.ApplicationProtocol.HTTP,
        default_target_groups=[blue],
    )
    test_listener = elbv2.ApplicationListener(
        stack,
        "Test",
        load_balancer=alb,
        port=8443,
        protocol=elbv2.ApplicationProtocol.HTTP,
        default_target_groups=[green],
    )
    BlueGreenDeployment(
        stack,
        "BlueGreen",
        props=BlueGreenDeploymentProps(
            application_name="cert-ra-app-staging",
            deployment_group_name="cert-ra-app-staging-dg",
            service=service,
            cluster=cluster,
            blue_target_group=blue,
            green_target_group=green,
            production_listener=production_listener,
            test_listener=test_listener,
            test_listener_url=_TEST_LISTENER_URL,
            ecr_repo_arn=_ECR_REPO_ARN,
            ecr_repo_name="cert-ra",
            cosign_pubkey_param_arn=_COSIGN_PARAM_ARN,
            vpc=vpc,
            private_subnets=list(vpc.private_subnets),
            alb_security_group=alb_sg,
            deployment_config=deployment_config,
            skip_tls_verify_in_smoke_test=skip_tls_verify,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_codedeploy_application_with_pinned_name() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::CodeDeploy::Application",
        {"ApplicationName": "cert-ra-app-staging", "ComputePlatform": "ECS"},
    )


def test_creates_codedeploy_deployment_group_with_pinned_name() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::CodeDeploy::DeploymentGroup",
        {"DeploymentGroupName": "cert-ra-app-staging-dg"},
    )


def test_default_deployment_config_is_linear_10pct_every_1m() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::CodeDeploy::DeploymentGroup",
        {"DeploymentConfigName": "CodeDeployDefault.ECSLinear10PercentEvery1Minutes"},
    )


def test_canary_deployment_config_can_be_overridden() -> None:
    template = _synth(
        deployment_config=codedeploy.EcsDeploymentConfig.CANARY_10_PERCENT_5_MINUTES
    )
    template.has_resource_properties(
        "AWS::CodeDeploy::DeploymentGroup",
        {"DeploymentConfigName": "CodeDeployDefault.ECSCanary10Percent5Minutes"},
    )


def test_creates_two_hook_lambdas() -> None:
    """One before-allow-traffic + one after-allow-traffic.

    The CodeDeploy framework may add its own helper Lambdas; we
    filter to our hooks via the description prefix.
    """
    template = _synth()
    fns = template.find_resources("AWS::Lambda::Function")
    cert_ra_hooks = [
        fn
        for fn in fns.values()
        if "cert-ra" in fn["Properties"].get("Description", "")
    ]
    assert len(cert_ra_hooks) == 2


def test_before_hook_has_ecr_read_scoped_to_repo() -> None:
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    matching: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "EcrReadImageMetadata":
                matching.append(stmt)
    assert len(matching) == 1
    assert matching[0]["Resource"] == _ECR_REPO_ARN


def test_before_hook_has_ssm_read_scoped_to_cosign_param() -> None:
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    matching: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "ReadCosignPubkey":
                matching.append(stmt)
    assert len(matching) == 1
    assert matching[0]["Resource"] == _COSIGN_PARAM_ARN


def test_before_hook_skip_tls_env_off_by_default() -> None:
    template = _synth()
    fns = template.find_resources(
        "AWS::Lambda::Function",
        {"Properties": {"Description": assertions.Match.string_like_regexp("Before")}},
    )
    (fn,) = fns.values()
    env_vars = fn["Properties"]["Environment"]["Variables"]
    assert env_vars["SMOKE_TEST_SKIP_TLS_VERIFY"] == "false"


def test_before_hook_skip_tls_env_on_when_requested() -> None:
    template = _synth(skip_tls_verify=True)
    fns = template.find_resources(
        "AWS::Lambda::Function",
        {"Properties": {"Description": assertions.Match.string_like_regexp("Before")}},
    )
    (fn,) = fns.values()
    env_vars = fn["Properties"]["Environment"]["Variables"]
    assert env_vars["SMOKE_TEST_SKIP_TLS_VERIFY"] == "true"


def test_auto_rollback_enabled_for_failures_stops_and_alarms() -> None:
    template = _synth()
    dgs = template.find_resources("AWS::CodeDeploy::DeploymentGroup")
    (dg,) = dgs.values()
    config = dg["Properties"]["AutoRollbackConfiguration"]
    assert config["Enabled"] is True
    events = set(config["Events"])
    assert events == {
        "DEPLOYMENT_FAILURE",
        "DEPLOYMENT_STOP_ON_REQUEST",
        "DEPLOYMENT_STOP_ON_ALARM",
    }


def test_deployment_group_references_two_alarms() -> None:
    template = _synth()
    dgs = template.find_resources("AWS::CodeDeploy::DeploymentGroup")
    (dg,) = dgs.values()
    alarms = dg["Properties"]["AlarmConfiguration"]
    assert alarms["Enabled"] is True
    assert len(alarms["Alarms"]) == 2


def test_creates_5xx_and_unhealthy_host_alarms() -> None:
    template = _synth()
    alarms = template.find_resources("AWS::CloudWatch::Alarm")
    metrics = {alarm["Properties"]["MetricName"] for alarm in alarms.values()}
    assert "HTTPCode_Target_5XX_Count" in metrics
    assert "UnHealthyHostCount" in metrics


def test_hook_lambdas_run_in_vpc() -> None:
    template = _synth()
    fns = template.find_resources("AWS::Lambda::Function")
    cert_ra_hooks = [
        fn
        for fn in fns.values()
        if "cert-ra" in fn["Properties"].get("Description", "")
    ]
    for fn in cert_ra_hooks:
        assert "VpcConfig" in fn["Properties"]
