# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from cdk_nag import NagSuppressions
from constructs import Construct

# The task family name `upgrade.sh` looks up when running migrations.
# Bumping this breaks the operator runbook — coordinate with the
# scripts before changing.
MIGRATION_TASK_FAMILY = "cert-ra-migrate"

# `certora-risk-api database upgrade` runs `alembic upgrade head`
# under the hood (wired via litestar-vite's database commands). The
# command lives in pyproject.toml's [project.scripts] entry for
# `certora-risk-api`.
#
# `--no-prompt` is critical for Fargate — without it the command
# emits `Are you sure you want migrate the database to the `head`
# revision? [y/n]:` to stdin, gets no answer (Fargate has no
# attached TTY), and aborts with `Aborted.` immediately. The task
# stops with exit code None (container killed before its own exit)
# and the operator chasing logs sees only the prompt + "Aborted",
# with no SQL error. Always pass --no-prompt for non-interactive
# invocations.
MIGRATION_COMMAND = ["certora-risk-api", "database", "upgrade", "--no-prompt"]

# Default Fargate sizing — migrations are short-lived and not CPU-
# bound. 0.25 vCPU + 0.5 GiB is plenty for `alembic upgrade head`.
DEFAULT_CPU = 256
DEFAULT_MEMORY_MIB = 512


@dataclass(frozen=True, slots=True)
class MigrationTaskProps:
    """Props for `MigrationTask`.

    The task is invoked via `aws ecs run-task` by `upgrade.sh` /
    `initial-setup.sh`. There's no service — the task definition is
    static and the run is operator-driven, so blast radius is gated
    by the IAM permission to call `ecs:RunTask` against this specific
    family.
    """

    cluster: ecs.ICluster
    """The ECS cluster the task runs in. Provided by MigrationsStack."""

    migrate_security_group: ec2.ISecurityGroup
    """`cert-ra-migrate-sg` from NetworkStack. RDS ingress is already
    wired on the rds SG; the migrate SG itself allows egress to RDS
    + AWS endpoints."""

    ecr_repo_arn: str
    ecr_repo_name: str
    ecr_cmk_arn: str
    image_tag: str
    """Image tag to deploy. The migration image is the same `cert-ra`
    image as the app — different entrypoint, same code. Operators
    pin to the same `sha-<git_sha>` as the AppStack deploy so the
    schema migration matches the app revision."""

    rds_master_secret_arn: str
    rds_master_secret_cmk_arn: str
    rds_endpoint: str
    rds_port: str
    """RDS connection params; the migration container reads via
    `DATABASE_*` env vars (same as the app for code-reuse)."""

    logs_cmk_arn: str
    """`cert-ra-logs-cmk` from ObservabilityStack."""

    cpu: int = DEFAULT_CPU
    memory_mib: int = DEFAULT_MEMORY_MIB
    log_retention: logs.RetentionDays = logs.RetentionDays.ONE_MONTH


class MigrationTask(Construct):
    """One-off Alembic migration task definition.

    No ECS service — operators invoke via `aws ecs run-task` from
    `upgrade.sh`. Schema-mutating DB privileges are owned by this
    task's role; the maintenance container has its own task role with
    interactive shell capability but app-level (read-only-ish) DB
    privileges. Splitting these gives us a cleaner audit trail and
    smaller blast radius (see § "Duplication-on-purpose:
    MaintenanceStack ↔ MigrationsStack" in the design spec).

    The migrate task ends as soon as `alembic upgrade head` returns;
    the runner script (`upgrade.sh` / `initial-setup.sh`) waits via
    `aws ecs wait tasks-stopped` and reads the container's exit code
    to decide whether to proceed.
    """

    task_definition: ecs.FargateTaskDefinition
    container: ecs.ContainerDefinition
    log_group: logs.LogGroup

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: MigrationTaskProps,
    ) -> None:
        super().__init__(scope, construct_id)
        self._props = props

        logs_cmk = kms.Key.from_key_arn(self, "LogsCmk", props.logs_cmk_arn)
        self.log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=f"/ecs/{MIGRATION_TASK_FAMILY}",
            retention=props.log_retention,
            encryption_key=logs_cmk,
            # See LitestarService for the DESTROY rationale.
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
            family=MIGRATION_TASK_FAMILY,
            # Image is built for linux/arm64 (build.yml --platform linux/arm64).
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        rds_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "RdsSecret", props.rds_master_secret_arn
        )
        rds_cmk = kms.Key.from_key_arn(self, "RdsCmk", props.rds_master_secret_cmk_arn)

        self.container = self.task_definition.add_container(
            "Migrate",
            image=ecs.ContainerImage.from_ecr_repository(ecr_repo, props.image_tag),
            essential=True,
            command=MIGRATION_COMMAND,
            environment={
                "DATABASE_HOST": props.rds_endpoint,
                "DATABASE_PORT": props.rds_port,
            },
            secrets={
                "DATABASE_USER": ecs.Secret.from_secrets_manager(
                    rds_secret, field="username"
                ),
                "DATABASE_PASSWORD": ecs.Secret.from_secrets_manager(
                    rds_secret, field="password"
                ),
            },
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="migrate",
                log_group=self.log_group,
            ),
        )

        execution_role = self.task_definition.obtain_execution_role()
        rds_cmk.grant_decrypt(execution_role)
        ecr_cmk = kms.Key.from_key_arn(self, "EcrCmk", props.ecr_cmk_arn)
        ecr_cmk.grant_decrypt(execution_role)

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
                        "CW Logs writes use wildcards on log-stream name; ECR "
                        "pulls use wildcards on layer digests. Neither is "
                        "predictable at deploy time."
                    ),
                },
                {
                    "id": "AwsSolutions-ECS2",
                    "reason": (
                        "DATABASE_HOST + DATABASE_PORT are non-secret RDS "
                        "endpoint info; credentials are injected as ECS Secrets."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @property
    def task_role(self) -> iam.IRole:
        """The container's runtime IAM role. Stack-level code grants
        schema-mutating DB privileges and any other migrate-specific
        resources (Secrets Manager reads, etc.) on this role."""
        return self.task_definition.task_role

    @property
    def task_definition_family(self) -> str:
        return self.task_definition.family

    @property
    def task_definition_arn(self) -> str:
        return self.task_definition.task_definition_arn
