# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs

from cert_ra_infra.constructs.temporal.schema_bootstrap import (
    TEMPORAL_ADMIN_TOOLS_IMAGE,
    TemporalSchemaBootstrap,
    TemporalSchemaBootstrapProps,
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
    sg = ec2.SecurityGroup(stack, "Sg", vpc=vpc, allow_all_outbound=False)
    TemporalSchemaBootstrap(
        stack,
        "SchemaBootstrap",
        props=TemporalSchemaBootstrapProps(
            cluster=cluster,
            vpc=vpc,
            private_subnets=list(vpc.private_subnets),
            security_group=sg,
            rds_endpoint="cert-ra-staging.cluster-abc.us-east-1.rds.amazonaws.com",
            rds_port="5432",
            rds_master_secret_arn=_RDS_SECRET_ARN,
            rds_master_secret_cmk_arn=_RDS_CMK_ARN,
            logs_cmk_arn=_LOGS_CMK_ARN,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_fargate_task_definition_with_known_family() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {"Family": "cert-ra-temporal-schema-bootstrap"},
    )


def test_task_uses_pinned_admin_tools_image() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    assert len(task_defs) == 1
    (td,) = task_defs.values()
    containers = td["Properties"]["ContainerDefinitions"]
    assert len(containers) == 1
    assert containers[0]["Image"] == TEMPORAL_ADMIN_TOOLS_IMAGE


def test_container_command_calls_temporal_sql_tool_for_both_databases() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    command = "".join(td["Properties"]["ContainerDefinitions"][0]["Command"])
    # Both create-database calls — temporal + temporal_visibility.
    assert "create-database --database temporal " in command
    assert "create-database --database temporal_visibility" in command
    # setup-schema runs for both databases.
    assert command.count("setup-schema") == 2
    # update-schema runs for both databases.
    assert command.count("update-schema") == 2


def test_container_env_has_rds_endpoint_and_port_as_plain_env() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    env_vars = {
        e["Name"]: e["Value"]
        for e in td["Properties"]["ContainerDefinitions"][0]["Environment"]
    }
    assert env_vars["POSTGRES_SEEDS"].startswith("cert-ra-staging.cluster")
    assert env_vars["DB_PORT"] == "5432"


def test_container_credentials_are_injected_as_ecs_secrets() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    secrets = {
        s["Name"]: s for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
    }
    assert {"POSTGRES_USER", "POSTGRES_PWD"} == set(secrets.keys())


def test_log_group_is_encrypted_with_provided_logs_cmk() -> None:
    template = _synth()
    log_groups = template.find_resources("AWS::Logs::LogGroup")
    assert len(log_groups) == 1
    (lg,) = log_groups.values()
    assert lg["Properties"]["KmsKeyId"] == _LOGS_CMK_ARN
    assert lg["Properties"]["LogGroupName"] == "/ecs/cert-ra-temporal-schema-bootstrap"


def test_execution_role_can_decrypt_rds_cmk() -> None:
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    decrypt_statements: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            action = stmt.get("Action")
            actions: list[object] = (
                list(action) if isinstance(action, list) else [action]  # pyright: ignore[reportUnknownArgumentType]
            )
            if "kms:Decrypt" in actions and stmt.get("Resource") == _RDS_CMK_ARN:
                decrypt_statements.append(stmt)
    assert len(decrypt_statements) >= 1


def test_task_uses_minimum_fargate_resources() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {"Cpu": "256", "Memory": "512"},
    )
