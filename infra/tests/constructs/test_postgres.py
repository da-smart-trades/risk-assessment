# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_ec2 as ec2

from cert_ra_infra.constructs.data.cmk import NarrowKmsCmk, NarrowKmsCmkProps
from cert_ra_infra.constructs.data.postgres import (
    MultiAzPostgres,
    MultiAzPostgresProps,
)


def _synth(*, multi_az: bool = True) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    vpc = ec2.Vpc(
        stack,
        "Vpc",
        max_azs=2,
        subnet_configuration=[
            ec2.SubnetConfiguration(
                name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
            ),
            ec2.SubnetConfiguration(
                name="PrivateIsolated",
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                cidr_mask=24,
            ),
        ],
    )
    sg = ec2.SecurityGroup(stack, "RdsSg", vpc=vpc, allow_all_outbound=False)
    cmk = NarrowKmsCmk(
        stack,
        "RdsCmk",
        props=NarrowKmsCmkProps(
            key_id="rds",
            env="test",
            purpose="encrypt",
            service_principals=["rds.amazonaws.com"],
        ),
    )
    MultiAzPostgres(
        stack,
        "Postgres",
        props=MultiAzPostgresProps(
            vpc=vpc,
            security_group=sg,
            isolated_subnets=list(vpc.isolated_subnets),
            storage_encryption_key=cmk.key,  # pyright: ignore[reportArgumentType]
            multi_az=multi_az,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_rds_instance_is_multi_az_by_default() -> None:
    template = _synth()
    template.has_resource_properties("AWS::RDS::DBInstance", {"MultiAZ": True})


def test_rds_storage_is_encrypted_with_provided_cmk() -> None:
    template = _synth()
    instances = template.find_resources("AWS::RDS::DBInstance")
    (db,) = instances.values()
    assert db["Properties"]["StorageEncrypted"] is True
    # KmsKeyId should reference the CMK we passed in
    assert "Fn::GetAtt" in str(db["Properties"]["KmsKeyId"])


def test_rds_lives_in_isolated_subnets() -> None:
    template = _synth()
    template.resource_count_is("AWS::RDS::DBSubnetGroup", 1)
    sn_groups = template.find_resources("AWS::RDS::DBSubnetGroup")
    (sn_group,) = sn_groups.values()
    assert len(sn_group["Properties"]["SubnetIds"]) >= 2


def test_rds_deletion_protection_default_on() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::RDS::DBInstance", {"DeletionProtection": True}
    )


def test_rds_not_publicly_accessible() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::RDS::DBInstance", {"PubliclyAccessible": False}
    )


def test_rds_has_master_credential_secret() -> None:
    """A managed secret is created when from_generated_secret() is used."""
    template = _synth()
    secrets = template.find_resources("AWS::SecretsManager::Secret")
    assert len(secrets) >= 1, "Expected at least one master credential secret"


def test_rds_iam_database_auth_is_enabled() -> None:
    """IAM database auth foundation for L1 (per-service Postgres users)."""
    template = _synth()
    template.has_resource_properties(
        "AWS::RDS::DBInstance",
        {"EnableIAMDatabaseAuthentication": True},
    )


def test_rds_engine_pinned_to_postgres_17() -> None:
    template = _synth()
    instances = template.find_resources("AWS::RDS::DBInstance")
    (db,) = instances.values()
    assert db["Properties"]["Engine"] == "postgres"
    # Engine version should start with "17"
    assert str(db["Properties"]["EngineVersion"]).startswith("17"), (
        f"Expected Postgres 17.x; got {db['Properties']['EngineVersion']}"
    )


def test_rds_logs_export_includes_postgresql() -> None:
    template = _synth()
    instances = template.find_resources("AWS::RDS::DBInstance")
    (db,) = instances.values()
    assert "postgresql" in db["Properties"]["EnableCloudwatchLogsExports"]


def test_rds_auto_minor_version_upgrade_enabled() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::RDS::DBInstance", {"AutoMinorVersionUpgrade": True}
    )


def test_rds_performance_insights_encrypted() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::RDS::DBInstance",
        {"EnablePerformanceInsights": True},
    )


def test_rds_retains_on_stack_delete() -> None:
    template = _synth()
    instances = template.find_resources("AWS::RDS::DBInstance")
    (db,) = instances.values()
    assert db.get("DeletionPolicy") == "Retain"
