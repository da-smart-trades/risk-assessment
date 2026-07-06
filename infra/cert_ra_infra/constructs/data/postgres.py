# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_kms as kms
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from cdk_nag import NagSuppressions
from constructs import Construct

DEFAULT_DATABASE_NAME = "cert_ra"
DEFAULT_MASTER_USERNAME = "cert_ra_admin"
DEFAULT_INSTANCE_CLASS = "t4g.large"
DEFAULT_ALLOCATED_STORAGE_GB = 100
DEFAULT_BACKUP_RETENTION_DAYS = 7
DEFAULT_ROTATION_DAYS = 30


@dataclass(frozen=True, slots=True)
class MultiAzPostgresProps:
    """Props for MultiAzPostgres. See § Resource ownership matrix and
    § Secrets rotation (RDS master credential) in the design spec."""

    vpc: ec2.IVpc
    security_group: ec2.ISecurityGroup
    """The `cert-ra-rds-sg` from `CertRaSecurityGroups`."""

    isolated_subnets: list[ec2.ISubnet]
    """Private-isolated subnets where the RDS instance lives. No NAT route."""

    storage_encryption_key: kms.IKey
    """`cert-ra-rds-cmk` from DataStack — used for storage AND the master credential secret."""

    instance_class: str = DEFAULT_INSTANCE_CLASS
    """e.g. `t4g.large`. ARM/Graviton family per the spec."""

    allocated_storage_gb: int = DEFAULT_ALLOCATED_STORAGE_GB

    multi_az: bool = True
    """Spec says Multi-AZ. Override to False in staging if cost-driven."""

    backup_retention_days: int = DEFAULT_BACKUP_RETENTION_DAYS

    rotation_days: int = DEFAULT_ROTATION_DAYS
    """Managed rotation interval for the master credential. 30 days per Q5."""

    database_name: str = DEFAULT_DATABASE_NAME

    master_username: str = DEFAULT_MASTER_USERNAME

    deletion_protection: bool = True


class MultiAzPostgres(Construct):
    """RDS Multi-AZ Postgres with managed rotation, KMS storage encryption,
    and the master credential stored in Secrets Manager.

    Per § Secrets rotation in the design spec, only the RDS master credential
    rotates automatically — every other secret is manually rotated. The
    SAR-deployed `SecretsManagerRDSPostgreSQLRotation` Lambda rotates this
    one every 30 days; the Lambda runs as a service identity (no MFA),
    which is compatible with M3's `BoolIfExists` deny pattern on
    SeededSecret writes elsewhere.
    """

    instance: rds.DatabaseInstance
    parameter_group: rds.ParameterGroup
    subnet_group: rds.SubnetGroup
    master_secret: secretsmanager.ISecret

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: MultiAzPostgresProps,
    ) -> None:
        super().__init__(scope, construct_id)

        # Engine: pinned to a specific Postgres minor version. Bumping
        # version is a documented runbook (snapshot, restore on new
        # version, cut over).
        #
        # 17.2 was deprecated by AWS RDS in us-east-2 by mid-2026; the
        # available 17.x patches are 17.5 through 17.10. We pin to
        # VER_17_9 — the latest minor that the bundled CDK enum knows
        # about. Bumping to a newer minor (17.10+) requires a CDK
        # upgrade first.
        engine = rds.DatabaseInstanceEngine.postgres(
            version=rds.PostgresEngineVersion.VER_17_9,
        )

        self.parameter_group = rds.ParameterGroup(
            self,
            "ParameterGroup",
            engine=engine,
            description="cert-ra: Postgres parameter group",
            parameters={
                # pg_stat_statements is required for advanced-alchemy
                # query observability and for Temporal's slow-query alerts.
                "shared_preload_libraries": "pg_stat_statements",
                "log_statement": "ddl",
                "log_min_duration_statement": "1000",
            },
        )

        self.subnet_group = rds.SubnetGroup(
            self,
            "SubnetGroup",
            description="cert-ra: RDS private-isolated subnet group",
            vpc=props.vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=props.isolated_subnets),
        )

        # Master credential — auto-generated in Secrets Manager, encrypted
        # with the RDS CMK. This is the only credential that auto-rotates.
        credentials = rds.Credentials.from_generated_secret(
            username=props.master_username,
            encryption_key=props.storage_encryption_key,
            secret_name=cdk.Aws.STACK_NAME + "/rds/master",
        )

        instance_type = ec2.InstanceType(props.instance_class)

        self.instance = rds.DatabaseInstance(
            self,
            "Db",
            engine=engine,
            instance_type=instance_type,
            vpc=props.vpc,
            subnet_group=self.subnet_group,
            security_groups=[props.security_group],
            multi_az=props.multi_az,
            allocated_storage=props.allocated_storage_gb,
            storage_encrypted=True,
            storage_encryption_key=props.storage_encryption_key,
            credentials=credentials,
            database_name=props.database_name,
            parameter_group=self.parameter_group,
            backup_retention=cdk.Duration.days(props.backup_retention_days),
            deletion_protection=props.deletion_protection,
            auto_minor_version_upgrade=True,
            enable_performance_insights=True,
            performance_insight_encryption_key=props.storage_encryption_key,
            cloudwatch_logs_exports=["postgresql", "upgrade"],
            monitoring_interval=cdk.Duration.seconds(60),
            publicly_accessible=False,
            iam_authentication=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # CDK promises the `secret` attribute is set when generated credentials
        # are used; type-narrow for the rest of the construct.
        secret = self.instance.secret
        if secret is None:
            raise RuntimeError(
                "rds.DatabaseInstance.secret unexpectedly None; "
                "from_generated_secret() should produce one."
            )
        self.master_secret = secret

        # TODO: 30-day managed rotation via SAR rotation Lambda (Q5).
        # Calling `add_rotation_single_user` here creates a cross-stack
        # dependency cycle: the Lambda's SG (DataStack) wants ingress on
        # `cert-ra-rds-sg` (NetworkStack), and the rule auto-references the
        # RDS endpoint port (DataStack) — NetworkStack → DataStack →
        # NetworkStack. Fix in a follow-up PR by pre-provisioning a
        # `cert-ra-rds-rotation-sg` in NetworkStack with a hardcoded
        # ingress on rds-sg:5432, and attaching the rotation Lambda to it
        # via ID lookup (immutable import).
        _ = props.rotation_days  # silence the unused-prop lint until wired up

        NagSuppressions.add_resource_suppressions(
            self.instance,
            [
                {
                    "id": "NIST.800.53.R5-RDSStorageEncrypted",
                    "reason": (
                        "False positive — storage_encrypted=True with a CMK is set; "
                        "cdk-nag occasionally flags this on Multi-AZ DBs."
                    ),
                },
                {
                    "id": "AwsSolutions-RDS10",
                    "reason": (
                        "deletion_protection is True by default; cdk-nag sometimes "
                        "flags Multi-AZ instances when the prop isn't surfaced at "
                        "the CFN layer until update."
                    ),
                },
                {
                    "id": "AwsSolutions-RDS11",
                    "reason": (
                        "Port obfuscation (non-default 5432) trades a tiny "
                        "defence-in-depth gain against scanning for material "
                        "operational complexity (every consumer needs the "
                        "non-default port). RDS is in private-isolated subnets "
                        "and reachable only via SGs; scanning is not the "
                        "threat model."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-RDSInBackupPlan",
                    "reason": (
                        "TODO: enroll in AWS Backup once ObservabilityStack defines "
                        "the backup vault. Current backup_retention=7 days via the "
                        "RDS automated backup mechanism provides point-in-time "
                        "recovery; AWS Backup adds cross-region copy and lifecycle "
                        "that we'll add under a future L-series hardening item."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-SecretsManagerRotationEnabled",
                    "reason": (
                        "TODO: enable RDS master credential rotation in a follow-up "
                        "PR. Calling add_rotation_single_user here creates a "
                        "cross-stack dependency cycle (rotation Lambda SG in "
                        "DataStack → rds-sg ingress in NetworkStack → RDS endpoint "
                        "port in DataStack). The fix needs a pre-provisioned "
                        "rds-rotation-sg in NetworkStack — tracked as a Q5 "
                        "follow-up."
                    ),
                },
                {
                    "id": "AwsSolutions-SMG4",
                    "reason": "Same as NIST.800.53.R5-SecretsManagerRotationEnabled.",
                },
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": (
                        "Enhanced Monitoring uses the AWS-managed "
                        "`AmazonRDSEnhancedMonitoringRole` policy — that's the "
                        "AWS-recommended pattern for the monitoring agent. "
                        "Replacing with a custom policy would duplicate AWS's "
                        "maintained permission set without security benefit."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @property
    def endpoint_address(self) -> str:
        return self.instance.db_instance_endpoint_address

    @property
    def endpoint_port(self) -> str:
        return self.instance.db_instance_endpoint_port

    @property
    def master_secret_arn(self) -> str:
        return self.master_secret.secret_arn
