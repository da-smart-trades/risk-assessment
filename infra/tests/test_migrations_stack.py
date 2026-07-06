# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.identity.gha_oidc_role import GitHubRepoIdentity
from cert_ra_infra.constructs.migrations.migration_task import (
    MIGRATION_TASK_FAMILY,
)
from cert_ra_infra.stacks._config import load_env
from cert_ra_infra.stacks.data import DataStack, DataStackProps
from cert_ra_infra.stacks.identity import IdentityStack, IdentityStackProps
from cert_ra_infra.stacks.migrations import MigrationsStack, MigrationsStackProps
from cert_ra_infra.stacks.network import NetworkStack
from cert_ra_infra.stacks.observability import (
    ObservabilityStack,
    ObservabilityStackProps,
)

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
    observability = ObservabilityStack(
        app,
        f"CertRa-ObservabilityStack-{cfg.env}",
        env=env,
        env_config=cfg,
        observability_props=ObservabilityStackProps(
            data=data, installer_role_arn_pattern=_INSTALLER_ARN
        ),
    )
    stack = MigrationsStack(
        app,
        f"CertRa-MigrationsStack-{cfg.env}",
        env=env,
        env_config=cfg,
        migrations_props=MigrationsStackProps(
            network=network,
            data=data,
            observability=observability,
            identity=identity,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_dedicated_ecs_cluster() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::ECS::Cluster", {"ClusterName": "cert-ra-migrations-staging"}
    )


def test_creates_migration_task_definition_only() -> None:
    """Exactly one task def; no Service."""
    template = _synth()
    template.resource_count_is("AWS::ECS::TaskDefinition", 1)
    template.resource_count_is("AWS::ECS::Service", 0)


def test_exports_upgrade_script_lookups() -> None:
    """`upgrade.sh` reads these three outputs via describe-stacks
    when running the migration task. The names are load-bearing —
    changing them breaks the script."""
    template = _synth()
    outputs = template.find_outputs("*")
    assert "ClusterName" in outputs
    assert "TaskDefinitionFamily" in outputs
    assert "MigrateSecurityGroupId" in outputs


def test_task_definition_family_output_matches_constant() -> None:
    template = _synth()
    outputs = template.find_outputs("TaskDefinitionFamily")
    (output,) = outputs.values()
    assert output["Value"] == MIGRATION_TASK_FAMILY
