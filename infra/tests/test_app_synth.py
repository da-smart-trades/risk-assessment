# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk

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

_INSTALLER_ARN = (
    "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/"
    "AWSReservedSSO_CertRaInstaller_*"
)


def test_all_stacks_synthesize_for_staging() -> None:
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
            network=network,
            installer_role_arn_pattern=_INSTALLER_ARN,
        ),
    )
    dns = DnsStack(app, f"CertRa-DnsStack-{cfg.env}", env=env, env_config=cfg)
    secrets = SecretsStack(
        app,
        f"CertRa-SecretsStack-{cfg.env}",
        env=env,
        env_config=cfg,
        secrets_props=SecretsStackProps(
            installer_role_arn_pattern=_INSTALLER_ARN,
        ),
    )
    observability = ObservabilityStack(
        app,
        f"CertRa-ObservabilityStack-{cfg.env}",
        env=env,
        env_config=cfg,
        observability_props=ObservabilityStackProps(
            data=data,
            installer_role_arn_pattern=_INSTALLER_ARN,
        ),
    )
    temporal = TemporalStack(
        app,
        f"CertRa-TemporalStack-{cfg.env}",
        env=env,
        env_config=cfg,
        mtls_enforce=True,
        temporal_props=TemporalStackProps(
            secrets=secrets,
            network=network,
            data=data,
            observability=observability,
        ),
    )
    AppStack(
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
    WorkersStack(
        app,
        f"CertRa-WorkersStack-{cfg.env}",
        env=env,
        env_config=cfg,
        workers_props=WorkersStackProps(
            network=network,
            data=data,
            secrets=secrets,
            observability=observability,
            identity=identity,
            temporal=temporal,
        ),
    )
    MigrationsStack(
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
    MaintenanceStack(
        app,
        f"CertRa-MaintenanceStack-{cfg.env}",
        env=env,
        env_config=cfg,
        maintenance_props=MaintenanceStackProps(
            network=network,
            data=data,
            secrets=secrets,
            observability=observability,
            temporal=temporal,
        ),
    )

    assembly = app.synth()
    stack_names = {s.stack_name for s in assembly.stacks}
    assert stack_names == {
        "CertRa-IdentityStack-staging",
        "CertRa-NetworkStack-staging",
        "CertRa-DataStack-staging",
        "CertRa-DnsStack-staging",
        "CertRa-SecretsStack-staging",
        "CertRa-ObservabilityStack-staging",
        "CertRa-TemporalStack-staging",
        "CertRa-AppStack-staging",
        "CertRa-WorkersStack-staging",
        "CertRa-MigrationsStack-staging",
        "CertRa-MaintenanceStack-staging",
    }
