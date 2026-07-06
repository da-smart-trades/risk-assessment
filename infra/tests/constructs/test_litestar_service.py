# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2

from cert_ra_infra.constructs.app.litestar_service import (
    HTTP_REDIRECT_LISTENER_PORT,
    PRODUCTION_LISTENER_PORT,
    TEST_LISTENER_PORT,
    AppSecretInjection,
    LitestarService,
    LitestarServiceProps,
)

_ECR_REPO_ARN = "arn:aws:ecr:us-east-1:111111111111:repository/cert-ra"
_ECR_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
)
_SECRETS_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
)
_RDS_SECRET_ARN = (
    "arn:aws:secretsmanager:us-east-1:111111111111:"
    "secret:/cert-ra/staging/data/rds/master-AbCdEf"
)
_RDS_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/11111111-2222-3333-4444-555555555555"
)
_LOGS_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/66666666-7777-8888-9999-aaaaaaaaaaaa"
)
_CERT_ARN = (
    "arn:aws:acm:us-east-1:111111111111:certificate/"
    "cccccccc-dddd-eeee-ffff-000000000000"
)
_SESSION_SECRET_ARN = (
    "arn:aws:secretsmanager:us-east-1:111111111111:"
    "secret:/cert-ra/staging/app/session-secret-AbCdEf"
)


def _synth(
    *,
    secret_injections: list[AppSecretInjection] | None = None,
) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    vpc = ec2.Vpc(stack, "Vpc", max_azs=2, nat_gateways=0)
    app_sg = ec2.SecurityGroup(stack, "AppSg", vpc=vpc, allow_all_outbound=True)
    alb_sg = ec2.SecurityGroup(stack, "AlbSg", vpc=vpc, allow_all_outbound=True)
    alb = elbv2.ApplicationLoadBalancer(
        stack,
        "Alb",
        vpc=vpc,
        internet_facing=True,
        security_group=alb_sg,
    )
    LitestarService(
        stack,
        "Litestar",
        props=LitestarServiceProps(
            service_name="cert-ra-app-staging",
            vpc=vpc,
            private_subnets=list(vpc.private_subnets),
            app_security_group=app_sg,
            alb=alb,
            alb_security_group=alb_sg,
            certificate_arn=_CERT_ARN,
            ecr_repo_arn=_ECR_REPO_ARN,
            ecr_repo_name="cert-ra",
            ecr_cmk_arn=_ECR_CMK_ARN,
            image_tag="latest",
            secrets_cmk_arn=_SECRETS_CMK_ARN,
            secret_injections=secret_injections or [],
            rds_master_secret_arn=_RDS_SECRET_ARN,
            rds_master_secret_cmk_arn=_RDS_CMK_ARN,
            rds_endpoint="cert-ra-staging.cluster-abc.us-east-1.rds.amazonaws.com",
            rds_port="5432",
            logs_cmk_arn=_LOGS_CMK_ARN,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_one_fargate_service() -> None:
    template = _synth()
    template.resource_count_is("AWS::ECS::Service", 1)


def test_service_uses_code_deploy_controller() -> None:
    """deployment_controller=CODE_DEPLOY means CDK registers task def
    revisions but doesn't drive rollouts — CodeDeploy does."""
    template = _synth()
    template.has_resource_properties(
        "AWS::ECS::Service",
        {"DeploymentController": {"Type": "CODE_DEPLOY"}},
    )


def test_creates_blue_and_green_target_groups() -> None:
    template = _synth()
    tgs = template.find_resources("AWS::ElasticLoadBalancingV2::TargetGroup")
    names = {tg["Properties"]["Name"] for tg in tgs.values()}
    assert names == {"cert-ra-app-staging-blue", "cert-ra-app-staging-green"}


def test_creates_production_test_and_redirect_listeners() -> None:
    template = _synth()
    listeners = template.find_resources("AWS::ElasticLoadBalancingV2::Listener")
    ports = {ln["Properties"]["Port"] for ln in listeners.values()}
    assert ports == {
        PRODUCTION_LISTENER_PORT,
        TEST_LISTENER_PORT,
        HTTP_REDIRECT_LISTENER_PORT,
    }


def test_http_redirect_listener_returns_301_to_https() -> None:
    template = _synth()
    listeners = template.find_resources(
        "AWS::ElasticLoadBalancingV2::Listener",
        {"Properties": {"Port": HTTP_REDIRECT_LISTENER_PORT}},
    )
    assert len(listeners) == 1
    (listener,) = listeners.values()
    actions = listener["Properties"]["DefaultActions"]
    assert len(actions) == 1
    redirect = actions[0]["RedirectConfig"]
    assert redirect["Protocol"] == "HTTPS"
    assert redirect["Port"] == str(PRODUCTION_LISTENER_PORT)
    assert redirect["StatusCode"] == "HTTP_301"
    # ALB-side host/path/query tokens preserve the original URL.
    assert redirect["Host"] == "#{host}"
    assert redirect["Path"] == "/#{path}"
    assert redirect["Query"] == "#{query}"


def test_production_listener_defaults_to_blue_target_group() -> None:
    template = _synth()
    listeners = template.find_resources(
        "AWS::ElasticLoadBalancingV2::Listener",
        {"Properties": {"Port": PRODUCTION_LISTENER_PORT}},
    )
    assert len(listeners) == 1
    (listener,) = listeners.values()
    actions = listener["Properties"]["DefaultActions"]
    # The default action references the blue target group by Ref. Find
    # the blue TG's logical id and assert against it.
    tgs = template.find_resources(
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        {"Properties": {"Name": "cert-ra-app-staging-blue"}},
    )
    blue_logical_id = next(iter(tgs.keys()))
    assert actions[0]["TargetGroupArn"]["Ref"] == blue_logical_id


def test_test_listener_defaults_to_green_target_group() -> None:
    template = _synth()
    listeners = template.find_resources(
        "AWS::ElasticLoadBalancingV2::Listener",
        {"Properties": {"Port": TEST_LISTENER_PORT}},
    )
    (listener,) = listeners.values()
    actions = listener["Properties"]["DefaultActions"]
    tgs = template.find_resources(
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        {"Properties": {"Name": "cert-ra-app-staging-green"}},
    )
    green_logical_id = next(iter(tgs.keys()))
    assert actions[0]["TargetGroupArn"]["Ref"] == green_logical_id


def test_https_listeners_use_recommended_tls_policy() -> None:
    """Production + test listeners must use the recommended TLS policy;
    the :80 redirect listener doesn't terminate TLS so it has no SslPolicy."""
    template = _synth()
    listeners = template.find_resources("AWS::ElasticLoadBalancingV2::Listener")
    for listener in listeners.values():
        if listener["Properties"]["Port"] == HTTP_REDIRECT_LISTENER_PORT:
            assert "SslPolicy" not in listener["Properties"]
            continue
        assert (
            listener["Properties"]["SslPolicy"] == "ELBSecurityPolicy-TLS13-1-2-2021-06"
        )


def test_secret_injections_become_ecs_secrets() -> None:
    template = _synth(
        secret_injections=[
            AppSecretInjection(
                env_var="SESSION_SECRET", secret_arn=_SESSION_SECRET_ARN
            ),
        ]
    )
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    secret_names = {
        s["Name"] for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
    }
    assert {"DATABASE_USER", "DATABASE_PASSWORD", "SESSION_SECRET"}.issubset(
        secret_names
    )


def test_no_secret_injections_skips_secrets_cmk_grant() -> None:
    """When `secret_injections=[]`, the secrets CMK isn't imported and
    no Decrypt grant on it should appear — saves an unused IAM statement
    in the execution role policy."""
    template = _synth(secret_injections=[])
    policies = template.find_resources("AWS::IAM::Policy")
    secrets_cmk_decrypts = 0
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            action = stmt.get("Action")
            actions: list[object] = (
                list(action)  # pyright: ignore[reportUnknownArgumentType]
                if isinstance(action, list)
                else [action]
            )
            if "kms:Decrypt" in actions and stmt.get("Resource") == _SECRETS_CMK_ARN:
                secrets_cmk_decrypts += 1
    assert secrets_cmk_decrypts == 0


def test_assigns_no_public_ip_to_service() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::ECS::Service",
        {
            "NetworkConfiguration": {
                "AwsvpcConfiguration": {"AssignPublicIp": "DISABLED"}
            }
        },
    )


def test_log_group_is_encrypted_with_provided_cmk() -> None:
    template = _synth()
    log_groups = template.find_resources("AWS::Logs::LogGroup")
    assert len(log_groups) == 1
    (lg,) = log_groups.values()
    assert lg["Properties"]["KmsKeyId"] == _LOGS_CMK_ARN
    assert lg["Properties"]["LogGroupName"] == "/ecs/cert-ra-app-staging"
