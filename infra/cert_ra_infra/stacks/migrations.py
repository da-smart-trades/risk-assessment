# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack
from aws_cdk import aws_ecs as ecs
from constructs import Construct

from cert_ra_infra.constructs.migrations.migration_task import (
    MIGRATION_TASK_FAMILY,
    MigrationTask,
    MigrationTaskProps,
)
from cert_ra_infra.stacks._config import EnvConfig
from cert_ra_infra.stacks.data import DataStack
from cert_ra_infra.stacks.identity import IdentityStack
from cert_ra_infra.stacks.network import NetworkStack
from cert_ra_infra.stacks.observability import ObservabilityStack

DEFAULT_IMAGE_TAG = "latest"


@dataclass(frozen=True, slots=True)
class MigrationsStackProps:
    """Stack-level inputs for MigrationsStack."""

    network: NetworkStack
    data: DataStack
    observability: ObservabilityStack
    identity: IdentityStack

    image_tag: str = DEFAULT_IMAGE_TAG
    """Image tag to deploy. Operators pin to the same `sha-<git_sha>`
    as the AppStack deploy so the schema migration matches the
    app revision."""


class MigrationsStack(Stack):
    """One-off Alembic migration runner.

    Provisions:
    - A dedicated ECS cluster (`cert-ra-migrations-${env}`). Sharing
      one cluster with AppStack / WorkersStack is tempting but
      Container Insights cost / control-plane noise is minimal here,
      and a separate cluster keeps the IAM ARN pattern for
      `ecs:RunTask` cleanly scoped (per § CertRaUpgrader's
      `EcsRunMigrationTaskOnly` Sid).
    - A `MigrationTask` task definition family `cert-ra-migrate`
      pulling the same `cert-ra` image at the configured tag and
      running `certora-risk-api database upgrade` (alembic upgrade
      head under the hood).

    No ECS service — operators invoke via `aws ecs run-task` from
    `upgrade.sh`. The runner script waits for the task to stop and
    fails the deploy on a non-zero exit code.

    CFN outputs match what `upgrade.sh` consumes:
    - `ClusterName` — the migrations cluster the script runs the task in
    - `TaskDefinitionFamily` — `cert-ra-migrate`
    - `MigrateSecurityGroupId` — `cert-ra-migrate-sg` from NetworkStack

    See § "Duplication-on-purpose: MaintenanceStack ↔ MigrationsStack"
    in the design spec for why this lives separate from MaintenanceStack
    even though both define one-off task definitions with `psql`/`alembic`.
    """

    cluster: ecs.Cluster
    migration_task: MigrationTask

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_config: EnvConfig,
        migrations_props: MigrationsStackProps,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_config = env_config

        # Dedicated cluster. Container Insights v2 on so the operator
        # can see the task's start/run/stop telemetry from CloudWatch.
        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name=f"cert-ra-migrations-{env_config.env}",
            vpc=migrations_props.network.vpc.vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        self.migration_task = MigrationTask(
            self,
            "MigrationTask",
            props=MigrationTaskProps(
                cluster=self.cluster,
                migrate_security_group=migrations_props.network.security_groups.migrate,
                ecr_repo_arn=migrations_props.identity.ecr.repository_arn,
                ecr_repo_name=(
                    migrations_props.identity.ecr.repository.repository_name
                ),
                ecr_cmk_arn=migrations_props.identity.ecr.encryption_cmk_arn,
                image_tag=migrations_props.image_tag,
                rds_master_secret_arn=migrations_props.data.postgres.master_secret_arn,
                rds_master_secret_cmk_arn=(migrations_props.data.rds_cmk.key.key_arn),
                rds_endpoint=migrations_props.data.postgres.endpoint_address,
                rds_port=migrations_props.data.postgres.endpoint_port,
                logs_cmk_arn=migrations_props.observability.logs_cmk.key.key_arn,
            ),
        )

        cdk.CfnOutput(
            self,
            "ClusterName",
            value=self.cluster.cluster_name,
            export_name=f"{self.stack_name}-ClusterName",
        )
        cdk.CfnOutput(
            self,
            "TaskDefinitionFamily",
            value=MIGRATION_TASK_FAMILY,
            export_name=f"{self.stack_name}-TaskDefinitionFamily",
        )
        cdk.CfnOutput(
            self,
            "MigrateSecurityGroupId",
            value=migrations_props.network.security_groups.migrate.security_group_id,
            export_name=f"{self.stack_name}-MigrateSecurityGroupId",
        )
