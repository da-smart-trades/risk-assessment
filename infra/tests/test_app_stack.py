# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.identity.gha_oidc_role import GitHubRepoIdentity
from cert_ra_infra.stacks._config import load_env
from cert_ra_infra.stacks.app import AppStack, AppStackProps
from cert_ra_infra.stacks.data import DataStack, DataStackProps
from cert_ra_infra.stacks.dns import DnsStack
from cert_ra_infra.stacks.identity import IdentityStack, IdentityStackProps
from cert_ra_infra.stacks.network import NetworkStack
from cert_ra_infra.stacks.observability import (
    ObservabilityStack,
    ObservabilityStackProps,
)
from cert_ra_infra.stacks.secrets import SecretsStack, SecretsStackProps

_INSTALLER_ARN = (
    "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/"
    "AWSReservedSSO_CertRaInstaller_*"
)


def _synth() -> assertions.Template:
    app = cdk.App()
    cfg = load_env("staging")
    env = cdk.Environment(account="111111111111", region=cfg.region)
    identity = IdentityStack(
        app,
        f"CertRa-IdentityStack-{cfg.env}",
        env=env,
        env_config=cfg,
        identity_props=IdentityStackProps(
            github_repo=GitHubRepoIdentity(
                owner="Certora", repo="risk-assessment"
            ),
            installer_role_arn_pattern=_INSTALLER_ARN,
        ),
    )
    network = NetworkStack(
        app, f"CertRa-NetworkStack-{cfg.env}", env=env, env_config=cfg
    )
    data = DataStack(
        app,
        f"CertRa-DataStack-{cfg.env}",
        env=env,
        env_config=cfg,
        data_props=DataStackProps(
            network=network, installer_role_arn_pattern=_INSTALLER_ARN
        ),
    )
    dns = DnsStack(app, f"CertRa-DnsStack-{cfg.env}", env=env, env_config=cfg)
    secrets = SecretsStack(
        app,
        f"CertRa-SecretsStack-{cfg.env}",
        env=env,
        env_config=cfg,
        secrets_props=SecretsStackProps(installer_role_arn_pattern=_INSTALLER_ARN),
    )
    observability = ObservabilityStack(
        app,
        f"CertRa-ObservabilityStack-{cfg.env}",
        env=env,
        env_config=cfg,
        observability_props=ObservabilityStackProps(
            data=data, installer_role_arn_pattern=_INSTALLER_ARN
        ),
    )
    stack = AppStack(
        app,
        f"CertRa-AppStack-{cfg.env}",
        env=env,
        env_config=cfg,
        app_props=AppStackProps(
            network=network,
            dns=dns,
            data=data,
            secrets=secrets,
            observability=observability,
            identity=identity,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_app_stack_exports_blue_and_green_tg_arns() -> None:
    template = _synth()
    outputs = template.find_outputs("*")
    output_names = set(outputs.keys())
    assert "BlueTargetGroupArn" in output_names
    assert "GreenTargetGroupArn" in output_names


def test_app_stack_exports_production_and_test_listener_arns() -> None:
    template = _synth()
    outputs = template.find_outputs("*")
    assert "ProductionListenerArn" in outputs
    assert "TestListenerArn" in outputs


def test_app_stack_wires_all_secret_injections() -> None:
    """All five app-runtime secrets must be mounted as ECS secrets on
    the task definition."""
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    secret_names = {
        s["Name"] for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
    }
    assert {
        "SESSION_SECRET",
        "OAUTH_PROVIDERS",
        "RPC_PROVIDERS",
        "RESEND_API_KEY",
        "SENTRY_DSN",
        "ANTHROPIC_API_KEY",
        "THE_GRAPH_API_KEY",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
    }.issubset(secret_names)


def test_app_stack_uses_default_image_tag_when_unspecified() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    image = td["Properties"]["ContainerDefinitions"][0]["Image"]
    # The image is a Fn::Join across ECR repo ARN parts ending in :latest.
    image_join = image["Fn::Join"][1]
    assert any(":latest" in str(part) for part in image_join)


def test_app_stack_exports_code_deploy_application_and_group_names() -> None:
    template = _synth()
    outputs = template.find_outputs("*")
    assert "CodeDeployApplicationName" in outputs
    assert "CodeDeployDeploymentGroupName" in outputs


def test_app_stack_exports_app_task_definition_arn_for_upgrade_script() -> None:
    """upgrade.sh reads this output via describe-stacks to build the
    CodeDeploy AppSpec for blue/green traffic shifts."""
    template = _synth()
    outputs = template.find_outputs("*")
    assert "AppTaskDefinitionArn" in outputs


def test_staging_uses_linear_deployment_config() -> None:
    """`env=staging` (the default in `load_env`) must select the
    linear-10%-every-1-minute config, not canary."""
    template = _synth()
    template.has_resource_properties(
        "AWS::CodeDeploy::DeploymentGroup",
        {"DeploymentConfigName": "CodeDeployDefault.ECSLinear10PercentEvery1Minutes"},
    )


def test_creates_route53_alias_records_for_apex_and_www() -> None:
    """The env's hosted zone gets two ARecords aliasing the apex AND
    the `www.` prefix to the public ALB. Both must be A-record aliases
    (CNAMEs at the apex are illegal per RFC 1034). The ACM wildcard
    SAN already covers `www.<domain>`."""
    template = _synth()
    records = template.find_resources(
        "AWS::Route53::RecordSet", {"Properties": {"Type": "A"}}
    )
    assert len(records) == 2
    for record in records.values():
        assert "AliasTarget" in record["Properties"]
    # One record is the apex (record_name == hosted_zone_name); the
    # other starts with `www.`.
    names = {record["Properties"]["Name"] for record in records.values()}
    assert any(name.startswith("www.") for name in names)


def test_route53_alias_targets_the_public_alb() -> None:
    template = _synth()
    records = template.find_resources(
        "AWS::Route53::RecordSet", {"Properties": {"Type": "A"}}
    )
    for record in records.values():
        alias = record["Properties"]["AliasTarget"]
        # The DNSName should be a Fn::Join referencing the imported ALB
        # DNS name. The HostedZoneId likewise references the ALB's
        # canonical zone — both populated by CDK's LoadBalancerTarget alias.
        assert "DNSName" in alias
        assert "HostedZoneId" in alias
