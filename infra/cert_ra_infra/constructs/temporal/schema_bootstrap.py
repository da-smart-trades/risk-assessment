# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from cdk_nag import NagSuppressions
from constructs import Construct

# Pinned admin-tools version. NOTE: temporalio/admin-tools patches
# independently of temporalio/server — there is no admin-tools 1.27.2 or
# 1.27.4 on Docker Hub; the latest 1.27.x is 1.27.1 (ships the postgresql
# v12 schema this construct uses, compatible with server 1.27.4). Verify a
# tag exists (`docker manifest inspect`) before bumping. Part of the
# Temporal upgrade runbook (Q4).
TEMPORAL_ADMIN_TOOLS_TAG = "1.27.1"
TEMPORAL_ADMIN_TOOLS_IMAGE = f"temporalio/admin-tools:{TEMPORAL_ADMIN_TOOLS_TAG}"

# Schema version directories shipped with the admin-tools image. These
# match the server version's expected schema; new server versions are
# accompanied by new directories under /etc/temporal/schema/.
SCHEMA_DIR = "/etc/temporal/schema/postgresql/v12"


@dataclass(frozen=True, slots=True)
class TemporalSchemaBootstrapProps:
    """Props for TemporalSchemaBootstrap.

    See § Initial-setup steps in the design spec — step 12 waits for
    Temporal RDS schema bootstrap to complete before deploying the
    Temporal services. This construct defines the Fargate task that
    `initial-setup.sh` invokes via `aws ecs run-task`.
    """

    cluster: ecs.ICluster
    """The ECS cluster the task runs in (reuses TemporalCluster's)."""

    vpc: ec2.IVpc
    private_subnets: list[ec2.ISubnet]

    security_group: ec2.ISecurityGroup
    """SG with egress to RDS:5432. Reuses the temporal-fe-sg for
    pragmatic reasons — schema bootstrap is a one-time operation that
    only needs RDS access."""

    rds_endpoint: str
    rds_port: str

    rds_master_secret_arn: str
    """Pass-by-string to avoid cross-stack cycles (same pattern as
    TemporalCluster — see its docstring)."""

    rds_master_secret_cmk_arn: str

    logs_cmk_arn: str
    """`cert-ra-logs-cmk` from ObservabilityStack for CW Logs encryption."""

    cpu: int = 256
    memory_mib: int = 512


class TemporalSchemaBootstrap(Construct):
    """Fargate task definition for the one-off Temporal schema setup.

    Operators invoke this via `aws ecs run-task` after TemporalStack is
    deployed but before any Temporal server starts. The task:

    1. Creates the `temporal` database
    2. Runs `temporal-sql-tool setup-schema -v 0.0` to install the base
       schema
    3. Runs `temporal-sql-tool update-schema -d <SCHEMA_DIR>/temporal/versioned`
       to apply all post-base migrations
    4. Repeats for the `temporal_visibility` database

    The task is RemovalPolicy-protected only via the task definition's
    revision history; deleting the stack doesn't drop the schema (RDS is
    in its own RETAIN-policy stack).
    """

    task_definition: ecs.FargateTaskDefinition
    log_group: logs.LogGroup

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: TemporalSchemaBootstrapProps,
    ) -> None:
        super().__init__(scope, construct_id)
        self._props = props

        logs_cmk = kms.Key.from_key_arn(self, "LogsCmk", props.logs_cmk_arn)
        self.log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name="/ecs/cert-ra-temporal-schema-bootstrap",
            retention=logs.RetentionDays.ONE_MONTH,
            encryption_key=logs_cmk,
            # See LitestarService for the DESTROY rationale.
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            cpu=props.cpu,
            memory_limit_mib=props.memory_mib,
            family="cert-ra-temporal-schema-bootstrap",
        )

        rds_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "RdsSecret", props.rds_master_secret_arn
        )
        rds_cmk = kms.Key.from_key_arn(self, "RdsCmk", props.rds_master_secret_cmk_arn)

        # Just the shell script; the "/bin/sh -c" wrapper is supplied via
        # entry_point on the container (see below). It must NOT be folded
        # into `command`: the admin-tools image's ENTRYPOINT is
        # `tini -- sleep infinity` (it's built to stay alive for exec), so
        # a bare `command=["/bin/sh","-c",...]` is appended as args to
        # `sleep infinity` and never runs — the task sleeps forever, logs
        # nothing, never connects to RDS, and `aws ecs wait tasks-stopped`
        # blocks until it times out. Overriding entry_point replaces the
        # sleep so the script actually executes.
        bootstrap_script = (
            # Schema setup is idempotent in two ways:
            # 1. `create-database` is a no-op if the DB already exists
            # 2. `setup-schema` records the schema version in
            #    schema_version table; `update-schema` is a no-op if
            #    nothing new applies.
            "set -eux; "
            "temporal-sql-tool "
            "--plugin postgres12 "
            "--ep $POSTGRES_SEEDS --port $DB_PORT "
            "--user $POSTGRES_USER --password $POSTGRES_PWD "
            # `--database` is a GLOBAL flag and must precede the subcommand;
            # `create-database --database X` is rejected ("flag not defined"),
            # so the DB was never created and setup-schema then hung
            # connecting to a non-existent database.
            "--database temporal create-database || true; "
            "temporal-sql-tool "
            "--plugin postgres12 "
            "--ep $POSTGRES_SEEDS --port $DB_PORT "
            "--user $POSTGRES_USER --password $POSTGRES_PWD "
            "--database temporal "
            f"setup-schema -v 0.0; "
            "temporal-sql-tool "
            "--plugin postgres12 "
            "--ep $POSTGRES_SEEDS --port $DB_PORT "
            "--user $POSTGRES_USER --password $POSTGRES_PWD "
            "--database temporal "
            f"update-schema -d {SCHEMA_DIR}/temporal/versioned; "
            "temporal-sql-tool "
            "--plugin postgres12 "
            "--ep $POSTGRES_SEEDS --port $DB_PORT "
            "--user $POSTGRES_USER --password $POSTGRES_PWD "
            "--database temporal_visibility create-database || true; "
            "temporal-sql-tool "
            "--plugin postgres12 "
            "--ep $POSTGRES_SEEDS --port $DB_PORT "
            "--user $POSTGRES_USER --password $POSTGRES_PWD "
            "--database temporal_visibility "
            f"setup-schema -v 0.0; "
            "temporal-sql-tool "
            "--plugin postgres12 "
            "--ep $POSTGRES_SEEDS --port $DB_PORT "
            "--user $POSTGRES_USER --password $POSTGRES_PWD "
            "--database temporal_visibility "
            f"update-schema -d {SCHEMA_DIR}/visibility/versioned"
        )

        self.task_definition.add_container(
            "Bootstrap",
            image=ecs.ContainerImage.from_registry(TEMPORAL_ADMIN_TOOLS_IMAGE),
            essential=True,
            # Override the image's `tini -- sleep infinity` ENTRYPOINT so the
            # bootstrap script runs instead of the container sleeping forever.
            entry_point=["/bin/sh", "-c"],
            command=[bootstrap_script],
            environment={
                "POSTGRES_SEEDS": props.rds_endpoint,
                "DB_PORT": props.rds_port,
                # RDS enforces SSL (rds.force_ssl=1) — a non-TLS connection is
                # rejected ("no pg_hba.conf entry ... no encryption"), which
                # made temporal-sql-tool retry forever and look like a hang.
                # temporal-sql-tool reads SQL_TLS / SQL_TLS_DISABLE_HOST_VERIFICATION.
                # Encrypt without server-cert verification (libpq sslmode=require
                # equivalent); no RDS CA bundle ships in the image.
                "SQL_TLS": "true",
                "SQL_TLS_DISABLE_HOST_VERIFICATION": "true",
            },
            secrets={
                "POSTGRES_USER": ecs.Secret.from_secrets_manager(
                    rds_secret, field="username"
                ),
                "POSTGRES_PWD": ecs.Secret.from_secrets_manager(
                    rds_secret, field="password"
                ),
            },
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="schema-bootstrap",
                log_group=self.log_group,
            ),
        )

        # Grant KMS Decrypt on the imported CMK (the imported secret's
        # auto-grants only add identity-side IAM, not KMS).
        rds_cmk.grant_decrypt(self.task_definition.obtain_execution_role())

        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                    "reason": (
                        "L2 FargateTaskDefinition auto-creates execution role "
                        "with an inline policy for secrets + CW Logs. We don't "
                        "author it."
                    ),
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "CW Logs writes use wildcards on log-stream name; "
                        "stream names aren't pre-computable."
                    ),
                },
                {
                    "id": "AwsSolutions-ECS2",
                    "reason": (
                        "POSTGRES_SEEDS and DB_PORT are non-secret RDS "
                        "endpoint info; the actual credentials are injected "
                        "as ECS secrets (not env vars)."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @property
    def task_definition_arn(self) -> str:
        return self.task_definition.task_definition_arn

    @property
    def task_family(self) -> str:
        return self.task_definition.family
