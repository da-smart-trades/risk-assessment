# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_ec2 as ec2

from cert_ra_infra.constructs.temporal.cluster import (
    NUM_HISTORY_SHARDS,
    TemporalCluster,
    TemporalClusterProps,
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
_SECRETS_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/dddddddd-eeee-ffff-0000-111111111111"
)
_FRONTEND_MTLS_SECRET_ARN = (
    "arn:aws:secretsmanager:us-east-1:111111111111:"
    "secret:/cert-ra/staging/temporal/mtls/temporal-frontend-AbCdEf"
)


def _synth(*, mtls_enforce: bool) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    vpc = ec2.Vpc(stack, "Vpc", max_azs=2, nat_gateways=0)
    sg = ec2.SecurityGroup(stack, "Sg", vpc=vpc, allow_all_outbound=False)
    TemporalCluster(
        stack,
        "Cluster",
        props=TemporalClusterProps(
            cluster_name="cert-ra-temporal-staging",
            vpc=vpc,
            private_subnets=list(vpc.private_subnets),
            temporal_fe_security_group=sg,
            alb_security_group=sg,
            rds_endpoint="cert-ra-staging.cluster-abc.us-east-1.rds.amazonaws.com",
            rds_port="5432",
            rds_master_secret_arn=_RDS_SECRET_ARN,
            rds_master_secret_cmk_arn=_RDS_CMK_ARN,
            logs_cmk_arn=_LOGS_CMK_ARN,
            mtls_enforce=mtls_enforce,
            frontend_mtls_secret_arn=_FRONTEND_MTLS_SECRET_ARN,
            secrets_cmk_arn=_SECRETS_CMK_ARN,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_four_temporal_fargate_services() -> None:
    template = _synth(mtls_enforce=False)
    template.resource_count_is("AWS::ECS::Service", 4)


def test_creates_four_task_definitions_one_per_service() -> None:
    template = _synth(mtls_enforce=False)
    template.resource_count_is("AWS::ECS::TaskDefinition", 4)


def test_num_history_shards_is_512() -> None:
    """Load-bearing invariant per § Load-bearing immutable values."""
    assert NUM_HISTORY_SHARDS == 512
    template = _synth(mtls_enforce=False)
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    for td in task_defs.values():
        env = {
            e["Name"]: e["Value"]
            for e in td["Properties"]["ContainerDefinitions"][0]["Environment"]
        }
        assert env["NUM_HISTORY_SHARDS"] == "512"


def test_mtls_enforce_off_does_not_mount_mtls_secrets() -> None:
    template = _synth(mtls_enforce=False)
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    for td in task_defs.values():
        secrets = {
            s["Name"]: s for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
        }
        # Only RDS creds are mounted as secrets when mTLS is off.
        assert set(secrets.keys()) == {"POSTGRES_USER", "POSTGRES_PWD"}


def test_mtls_enforce_off_sets_require_client_auth_false() -> None:
    template = _synth(mtls_enforce=False)
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    for td in task_defs.values():
        env = {
            e["Name"]: e["Value"]
            for e in td["Properties"]["ContainerDefinitions"][0]["Environment"]
        }
        assert env["TEMPORAL_TLS_REQUIRE_CLIENT_AUTH"] == "false"


def test_mtls_enforce_on_mounts_cert_key_chain_as_ecs_secrets() -> None:
    template = _synth(mtls_enforce=True)
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    assert len(task_defs) == 4
    for td in task_defs.values():
        secrets = {
            s["Name"]: s for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
        }
        # Three new MTLS_* secrets injected on top of the RDS creds.
        assert {
            "POSTGRES_USER",
            "POSTGRES_PWD",
            "MTLS_CERT_CONTENT",
            "MTLS_KEY_CONTENT",
            "MTLS_CHAIN_CONTENT",
        }.issubset(set(secrets.keys()))


def test_mtls_enforce_on_sets_require_client_auth_true() -> None:
    template = _synth(mtls_enforce=True)
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    for td in task_defs.values():
        env = {
            e["Name"]: e["Value"]
            for e in td["Properties"]["ContainerDefinitions"][0]["Environment"]
        }
        assert env["TEMPORAL_TLS_REQUIRE_CLIENT_AUTH"] == "true"


def test_mtls_enforce_on_grants_decrypt_on_secrets_cmk() -> None:
    template = _synth(mtls_enforce=True)
    policies = template.find_resources("AWS::IAM::Policy")
    decrypt_on_secrets_cmk: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            action = stmt.get("Action")
            actions: list[object] = (
                list(action)  # pyright: ignore[reportUnknownArgumentType]
                if isinstance(action, list)
                else [action]
            )
            if "kms:Decrypt" in actions and stmt.get("Resource") == _SECRETS_CMK_ARN:
                decrypt_on_secrets_cmk.append(stmt)
    # One per service execution role (4 services).
    assert len(decrypt_on_secrets_cmk) == 4


def test_internal_nlb_has_deletion_protection_enabled() -> None:
    template = _synth(mtls_enforce=False)
    nlbs = template.find_resources("AWS::ElasticLoadBalancingV2::LoadBalancer")
    assert len(nlbs) == 1
    (nlb,) = nlbs.values()
    attrs = {a["Key"]: a["Value"] for a in nlb["Properties"]["LoadBalancerAttributes"]}
    assert attrs["deletion_protection.enabled"] == "true"


def test_log_groups_are_encrypted_with_logs_cmk() -> None:
    template = _synth(mtls_enforce=False)
    log_groups = template.find_resources("AWS::Logs::LogGroup")
    # Four service log groups.
    assert len(log_groups) >= 4
    for lg in log_groups.values():
        assert lg["Properties"]["KmsKeyId"] == _LOGS_CMK_ARN
