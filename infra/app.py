# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import os

import aws_cdk as cdk
import cdk_nag

from cert_ra_infra.config import load_config
from cert_ra_infra.constructs.identity.gha_oidc_role import GitHubRepoIdentity
from cert_ra_infra.stacks import (
    AppStack,
    DataStack,
    DnsStack,
    IdentityStack,
    MaintenanceStack,
    MigrationsStack,
    NetworkStack,
    ObservabilityStack,
    SecretsStack,
    TemporalStack,
    WorkersStack,
    load_env,
)
from cert_ra_infra.stacks.app import AppStackProps
from cert_ra_infra.stacks.data import DataStackProps
from cert_ra_infra.stacks.identity import IdentityStackProps
from cert_ra_infra.stacks.maintenance import MaintenanceStackProps
from cert_ra_infra.stacks.migrations import MigrationsStackProps
from cert_ra_infra.stacks.observability import ObservabilityStackProps
from cert_ra_infra.stacks.secrets import SecretsStackProps
from cert_ra_infra.stacks.temporal import TemporalStackProps
from cert_ra_infra.stacks.workers import WorkersStackProps

# GitHub repo identity is environment-independent — same repo flows to both envs.
# Owner/repo come from the bootstrap deployment config so a fork can trust its own
# repo's OIDC tokens without editing this source.
_GITHUB_CONFIG = load_config()["github"]
_GITHUB_REPO = GitHubRepoIdentity(
    owner=_GITHUB_CONFIG["owner"], repo=_GITHUB_CONFIG["repo"]
)

# The CertRaInstaller permission-set roles created by IAM Identity Center
# follow a wildcard ARN pattern. Match-any across the env-suffixed paths.
_INSTALLER_ROLE_ARN_PATTERN = (
    "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/"
    "AWSReservedSSO_CertRaInstaller_*"
)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"true", "1", "yes", "on"}


def main() -> None:
    app = cdk.App()

    env_name = os.environ.get("CDK_ENV", "staging")
    env_config = load_env(env_name)

    app.node.set_context(
        "temporal_mtls_enforce",
        _bool_env("CDK_TEMPORAL_MTLS_ENFORCE", default=True),
    )

    cdk_env = cdk.Environment(region=env_config.region)
    suffix = env_config.env

    identity = IdentityStack(
        app,
        f"CertRa-IdentityStack-{suffix}",
        env=cdk_env,
        env_config=env_config,
        identity_props=IdentityStackProps(
            github_repo=_GITHUB_REPO,
            installer_role_arn_pattern=_INSTALLER_ROLE_ARN_PATTERN,
        ),
    )
    network = NetworkStack(
        app, f"CertRa-NetworkStack-{suffix}", env=cdk_env, env_config=env_config
    )
    data = DataStack(
        app,
        f"CertRa-DataStack-{suffix}",
        env=cdk_env,
        env_config=env_config,
        data_props=DataStackProps(
            network=network,
            installer_role_arn_pattern=_INSTALLER_ROLE_ARN_PATTERN,
        ),
    )
    dns = DnsStack(app, f"CertRa-DnsStack-{suffix}", env=cdk_env, env_config=env_config)
    secrets = SecretsStack(
        app,
        f"CertRa-SecretsStack-{suffix}",
        env=cdk_env,
        env_config=env_config,
        secrets_props=SecretsStackProps(
            installer_role_arn_pattern=_INSTALLER_ROLE_ARN_PATTERN,
        ),
    )
    observability = ObservabilityStack(
        app,
        f"CertRa-ObservabilityStack-{suffix}",
        env=cdk_env,
        env_config=env_config,
        observability_props=ObservabilityStackProps(
            data=data,
            installer_role_arn_pattern=_INSTALLER_ROLE_ARN_PATTERN,
        ),
    )
    temporal = TemporalStack(
        app,
        f"CertRa-TemporalStack-{suffix}",
        env=cdk_env,
        env_config=env_config,
        mtls_enforce=app.node.try_get_context("temporal_mtls_enforce"),
        temporal_props=TemporalStackProps(
            secrets=secrets,
            network=network,
            data=data,
            observability=observability,
        ),
    )
    AppStack(
        app,
        f"CertRa-AppStack-{suffix}",
        env=cdk_env,
        env_config=env_config,
        app_props=AppStackProps(
            network=network,
            dns=dns,
            data=data,
            secrets=secrets,
            observability=observability,
            identity=identity,
            temporal=temporal,
            image_tag=os.environ.get("CDK_APP_IMAGE_TAG", "latest"),
            # `bootstrap=True` lands a service with desired_count=0
            # so CFN doesn't wait on tasks for a not-yet-pushed image.
            # Only initial-setup.sh's first AppStack create should set
            # this — recovery scripts + upgrade.sh must leave it
            # False so the service runs at DEFAULT_DESIRED_COUNT.
            bootstrap=_bool_env("CDK_APP_BOOTSTRAP", default=False),
        ),
    )
    WorkersStack(
        app,
        f"CertRa-WorkersStack-{suffix}",
        env=cdk_env,
        env_config=env_config,
        workers_props=WorkersStackProps(
            network=network,
            data=data,
            secrets=secrets,
            observability=observability,
            identity=identity,
            temporal=temporal,
            image_tag=os.environ.get("CDK_APP_IMAGE_TAG", "latest"),
        ),
    )
    MigrationsStack(
        app,
        f"CertRa-MigrationsStack-{suffix}",
        env=cdk_env,
        env_config=env_config,
        migrations_props=MigrationsStackProps(
            network=network,
            data=data,
            observability=observability,
            identity=identity,
            image_tag=os.environ.get("CDK_APP_IMAGE_TAG", "latest"),
        ),
    )
    MaintenanceStack(
        app,
        f"CertRa-MaintenanceStack-{suffix}",
        env=cdk_env,
        env_config=env_config,
        maintenance_props=MaintenanceStackProps(
            network=network,
            data=data,
            secrets=secrets,
            observability=observability,
            temporal=temporal,
        ),
    )

    cdk.Aspects.of(app).add(cdk_nag.AwsSolutionsChecks(verbose=True))
    cdk.Aspects.of(app).add(cdk_nag.NIST80053R5Checks(verbose=True))

    app.synth()


if __name__ == "__main__":
    main()
