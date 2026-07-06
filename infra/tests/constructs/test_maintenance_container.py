# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs

from cert_ra_infra.constructs.ops.maintenance_container import (
    MAINT_COMMAND,
    MaintenanceContainer,
    MaintenanceContainerProps,
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
_MAINT_MTLS_ARN = (
    "arn:aws:secretsmanager:us-east-1:111111111111:"
    "secret:/cert-ra/staging/temporal/mtls/maint-AbCdEf"
)
_TEMPORAL_ENDPOINT = (
    "cert-ra-temporal-staging-internal-nlb-abc.elb.us-east-1.amazonaws.com"
)


def _flatten_join_parts(items: list[object]) -> list[str]:
    """Walk CFN Fn::Join blocks and return their literal string parts.

    CDK renders ARNs that interpolate `cdk.Aws.ACCOUNT_ID` /
    `cdk.Aws.REGION` as `{"Fn::Join": ["", [..., "/cert-ra/.../foo"]]}`.
    To assert on the literal subdomain pattern we have to walk into
    the join's parts list and pull out the strings.
    """
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
            continue
        if isinstance(item, dict) and "Fn::Join" in item:
            join_value = item["Fn::Join"]  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(join_value, list) or len(join_value) < 2:  # pyright: ignore[reportUnknownArgumentType]
                continue
            parts = join_value[1]  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(parts, list):
                continue
            for p in parts:  # pyright: ignore[reportUnknownVariableType]
                if isinstance(p, str):
                    out.append(p)
    return out


def _synth() -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    vpc = ec2.Vpc(stack, "Vpc", max_azs=2, nat_gateways=0)
    cluster = ecs.Cluster(stack, "Cluster", vpc=vpc)
    # Maint SG mirrors NetworkStack: allow_all_outbound=False.
    maint_sg = ec2.SecurityGroup(stack, "MaintSg", vpc=vpc, allow_all_outbound=False)
    # Two placeholder VPC endpoint SGs.
    endpoint_sg_1 = ec2.SecurityGroup(
        stack, "EndpointSg1", vpc=vpc, allow_all_outbound=False
    )
    endpoint_sg_2 = ec2.SecurityGroup(
        stack, "EndpointSg2", vpc=vpc, allow_all_outbound=False
    )
    MaintenanceContainer(
        stack,
        "Maint",
        props=MaintenanceContainerProps(
            service_name="cert-ra-maint-staging",
            env_name="staging",
            cluster=cluster,
            vpc=vpc,
            private_subnets=list(vpc.private_subnets),
            maint_security_group=maint_sg,
            vpc_endpoint_security_groups=[endpoint_sg_1, endpoint_sg_2],
            rds_master_secret_arn=_RDS_SECRET_ARN,
            rds_master_secret_cmk_arn=_RDS_CMK_ARN,
            rds_endpoint="cert-ra-staging.cluster-abc.us-east-1.rds.amazonaws.com",
            rds_port="5432",
            secrets_cmk_arn=_SECRETS_CMK_ARN,
            maint_mtls_secret_arn=_MAINT_MTLS_ARN,
            temporal_frontend_endpoint=_TEMPORAL_ENDPOINT,
            temporal_tls_server_name="temporal-frontend.cert-ra.local",
            logs_cmk_arn=_LOGS_CMK_ARN,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_one_fargate_service_with_desired_count_one() -> None:
    template = _synth()
    template.resource_count_is("AWS::ECS::Service", 1)
    services = template.find_resources("AWS::ECS::Service")
    (svc,) = services.values()
    assert svc["Properties"]["DesiredCount"] == 1


def test_enable_execute_command_is_true() -> None:
    """ECS Exec is the whole point of the maint container — operators
    need `aws ecs execute-command` to drop into a shell."""
    template = _synth()
    services = template.find_resources("AWS::ECS::Service")
    (svc,) = services.values()
    assert svc["Properties"]["EnableExecuteCommand"] is True


def test_container_command_is_sleep_infinity() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    command = td["Properties"]["ContainerDefinitions"][0]["Command"]
    assert command == MAINT_COMMAND
    assert command == ["sleep", "infinity"]


def test_maint_mtls_cert_key_chain_mounted_as_ecs_secrets() -> None:
    """The maint container talks to Temporal via the temporal CLI
    wrapper, which needs the maint mTLS triplet."""
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    secret_names = {
        s["Name"] for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
    }
    assert {
        "TEMPORAL_TLS_CLIENT_CERT_CONTENT",
        "TEMPORAL_TLS_CLIENT_KEY_CONTENT",
        "TEMPORAL_TLS_CA_CERT_CONTENT",
    }.issubset(secret_names)


def test_task_role_has_explicit_deny_on_peer_mtls_secrets() -> None:
    """A4: a compromised maint container must NOT be able to read any
    other service's mTLS material — otherwise it could impersonate a
    worker, the internal-worker role, or the app at Temporal."""
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    deny_stmts: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "DenyReadPeerMtlsSecrets":
                deny_stmts.append(stmt)
    assert len(deny_stmts) == 1
    stmt = deny_stmts[0]
    assert stmt["Effect"] == "Deny"
    resources = stmt["Resource"]
    assert isinstance(resources, list)
    # Each resource ARN is a Fn::Join because account/region are
    # CDK tokens. The literal subdomain pattern lives in the last
    # element of the join.
    flat = _flatten_join_parts(list(resources))  # pyright: ignore[reportUnknownArgumentType]
    assert any("temporal/mtls/worker-*" in s for s in flat)
    assert any("temporal/mtls/internal-worker" in s for s in flat)
    assert any("temporal/mtls/app" in s for s in flat)


def test_task_role_grants_wildcard_secrets_read_under_env() -> None:
    """Maint operators need to read the app/OAuth/RPC secrets too."""
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    allow_stmts: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "ReadEnvSecrets":
                allow_stmts.append(stmt)
    assert len(allow_stmts) == 1
    stmt = allow_stmts[0]
    assert stmt["Effect"] == "Allow"
    resources = stmt["Resource"]
    resource_list: list[object] = (
        list(resources)  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(resources, list)
        else [resources]
    )
    flat = _flatten_join_parts(resource_list)
    assert any("/cert-ra/staging/*" in s for s in flat)


def test_task_role_has_ecs_exec_channel_permissions() -> None:
    """Without ssmmessages:* the ECS Exec SSM agent inside the
    container cannot open its control + data channels."""
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    exec_stmts: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "EcsExecChannels":
                exec_stmts.append(stmt)
    assert len(exec_stmts) == 1
    actions = exec_stmts[0]["Action"]
    actions_set: set[object] = (
        set(actions)  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(actions, list)
        else {actions}
    )
    assert {
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
    }.issubset(actions_set)


def test_maint_sg_gets_egress_to_each_vpc_endpoint_sg() -> None:
    """Without these egress rules the maint SG drops AWS SDK calls
    (allow_all_outbound=False)."""
    template = _synth()
    rules = template.find_resources("AWS::EC2::SecurityGroupEgress")
    # Two endpoint SGs in this test, so we expect at least two
    # egress rules to 443. CDK may also auto-emit other rules; we
    # filter to TCP/443 against destination SG.
    matching = [
        r
        for r in rules.values()
        if r["Properties"].get("FromPort") == 443
        and r["Properties"].get("ToPort") == 443
        and r["Properties"].get("IpProtocol") == "tcp"
    ]
    assert len(matching) >= 2


def test_service_does_not_assign_public_ip() -> None:
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
    assert lg["Properties"]["LogGroupName"] == "/ecs/cert-ra-maint-staging"
