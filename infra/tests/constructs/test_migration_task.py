# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs

from cert_ra_infra.constructs.migrations.migration_task import (
    MIGRATION_COMMAND,
    MIGRATION_TASK_FAMILY,
    MigrationTask,
    MigrationTaskProps,
)

_ECR_REPO_ARN = "arn:aws:ecr:us-east-1:111111111111:repository/cert-ra"
_ECR_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
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


def _synth() -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    vpc = ec2.Vpc(stack, "Vpc", max_azs=2, nat_gateways=0)
    cluster = ecs.Cluster(stack, "Cluster", vpc=vpc)
    migrate_sg = ec2.SecurityGroup(stack, "MigrateSg", vpc=vpc, allow_all_outbound=True)
    MigrationTask(
        stack,
        "MigrationTask",
        props=MigrationTaskProps(
            cluster=cluster,
            migrate_security_group=migrate_sg,
            ecr_repo_arn=_ECR_REPO_ARN,
            ecr_repo_name="cert-ra",
            ecr_cmk_arn=_ECR_CMK_ARN,
            image_tag="latest",
            rds_master_secret_arn=_RDS_SECRET_ARN,
            rds_master_secret_cmk_arn=_RDS_CMK_ARN,
            rds_endpoint="cert-ra-staging.cluster-abc.us-east-1.rds.amazonaws.com",
            rds_port="5432",
            logs_cmk_arn=_LOGS_CMK_ARN,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_task_definition_with_pinned_family() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::ECS::TaskDefinition", {"Family": MIGRATION_TASK_FAMILY}
    )


def test_does_not_create_an_ecs_service() -> None:
    """MigrationTask is a one-off — no Service, no desired count, no
    autoscaling. Operators invoke via `aws ecs run-task`."""
    template = _synth()
    template.resource_count_is("AWS::ECS::Service", 0)


def test_task_definition_runs_database_upgrade_command() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    command = td["Properties"]["ContainerDefinitions"][0]["Command"]
    assert command == MIGRATION_COMMAND
    # Sanity: confirm it actually says "database upgrade" + the
    # non-interactive flag (without it, the command stalls on a TTY
    # prompt under Fargate and auto-aborts).
    assert command[1:3] == ["database", "upgrade"]
    assert "--no-prompt" in command


def test_db_credentials_injected_as_ecs_secrets() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    secret_names = {
        s["Name"] for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
    }
    assert {"DATABASE_USER", "DATABASE_PASSWORD"}.issubset(secret_names)


def test_db_endpoint_in_plain_env() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    env_vars = {
        e["Name"]: e["Value"]
        for e in td["Properties"]["ContainerDefinitions"][0]["Environment"]
    }
    assert env_vars["DATABASE_HOST"].startswith("cert-ra-staging.cluster")
    assert env_vars["DATABASE_PORT"] == "5432"


def test_log_group_is_encrypted_with_provided_cmk() -> None:
    template = _synth()
    log_groups = template.find_resources("AWS::Logs::LogGroup")
    assert len(log_groups) == 1
    (lg,) = log_groups.values()
    assert lg["Properties"]["KmsKeyId"] == _LOGS_CMK_ARN
    assert lg["Properties"]["LogGroupName"] == f"/ecs/{MIGRATION_TASK_FAMILY}"


def test_execution_role_grants_decrypt_on_rds_cmk() -> None:
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    decrypt_on_rds_cmk: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            action = stmt.get("Action")
            actions: list[object] = (
                list(action)  # pyright: ignore[reportUnknownArgumentType]
                if isinstance(action, list)
                else [action]
            )
            if "kms:Decrypt" in actions and stmt.get("Resource") == _RDS_CMK_ARN:
                decrypt_on_rds_cmk.append(stmt)
    assert len(decrypt_on_rds_cmk) >= 1
