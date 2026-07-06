# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack
from aws_cdk import aws_s3 as s3
from constructs import Construct

from cert_ra_infra.constructs.data.cmk import NarrowKmsCmk, NarrowKmsCmkProps
from cert_ra_infra.constructs.data.encrypted_bucket import (
    EncryptedBucket,
    EncryptedBucketProps,
)
from cert_ra_infra.constructs.data.postgres import MultiAzPostgres, MultiAzPostgresProps
from cert_ra_infra.stacks._config import EnvConfig
from cert_ra_infra.stacks.network import NetworkStack


@dataclass(frozen=True, slots=True)
class DataStackProps:
    """Stack-level inputs for DataStack.

    Wired from outside via cross-stack reference: the `network` arg pulls
    VPC, isolated subnets, and the rds security group from NetworkStack.
    `installer_role_arn_pattern` matches the IAM Identity Center-provisioned
    Installer roles for use in CMK admin policies.
    """

    network: NetworkStack
    installer_role_arn_pattern: str


class DataStack(Stack):
    """Foundation data — RDS Multi-AZ Postgres, two S3 buckets, and the
    per-data-class KMS CMKs (rds-cmk + s3-cmk).

    Per the resource ownership matrix, this stack owns:
    - `cert-ra-rds-cmk` (used for RDS storage + master credential secret)
    - `cert-ra-s3-cmk` (shared by the logs + assets buckets)
    - MultiAzPostgres (RDS Multi-AZ + parameter group + master cred secret
      with 30-day managed rotation per Q5)
    - EncryptedBucket x 2: `cert-ra-logs-{env}` and `cert-ra-assets-{env}`
    """

    rds_cmk: NarrowKmsCmk
    s3_cmk: NarrowKmsCmk
    postgres: MultiAzPostgres
    logs_bucket: EncryptedBucket
    assets_bucket: EncryptedBucket

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_config: EnvConfig,
        data_props: DataStackProps,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_config = env_config

        # CMKs — one per data class (M2). Admin only via the Installer role.
        # `cert-ra-rds-cmk` doubles as the master credential secret's CMK
        # since the secret is RDS-scoped; M2 allows this pragmatic
        # consolidation when both encrypt RDS-related material.
        self.rds_cmk = NarrowKmsCmk(
            self,
            "RdsCmk",
            props=NarrowKmsCmkProps(
                key_id="rds",
                env=env_config.env,
                purpose="encrypt",
                service_principals=[
                    "rds.amazonaws.com",
                    "secretsmanager.amazonaws.com",
                ],
                admin_roles=[data_props.installer_role_arn_pattern],
            ),
        )

        self.s3_cmk = NarrowKmsCmk(
            self,
            "S3Cmk",
            props=NarrowKmsCmkProps(
                key_id="s3",
                env=env_config.env,
                purpose="encrypt",
                service_principals=["s3.amazonaws.com"],
                # SSE-KMS PutObject / GetObject calls run with the
                # caller's IAM (the app's task role) — without this
                # delegation the key policy would block them even
                # though the task role's identity policy allows the
                # action. The condition restricts the grant to calls
                # routed through S3 in this region + this account.
                delegate_via_services=["s3"],
                admin_roles=[data_props.installer_role_arn_pattern],
            ),
        )

        # RDS Multi-AZ Postgres. Lives in the private-isolated subnets;
        # ingress only from rds-sg consumers (app/worker/maint/migrate).
        self.postgres = MultiAzPostgres(
            self,
            "Postgres",
            props=MultiAzPostgresProps(
                vpc=data_props.network.vpc.vpc,
                security_group=data_props.network.security_groups.rds,
                isolated_subnets=data_props.network.vpc.private_isolated_subnets,
                storage_encryption_key=self.rds_cmk.key,  # pyright: ignore[reportArgumentType]
            ),
        )

        # Two S3 buckets — logs (CloudTrail + app log archive) and assets
        # (frontend asset backups). Both use the s3-cmk.
        self.logs_bucket = EncryptedBucket(
            self,
            "LogsBucket",
            props=EncryptedBucketProps(
                bucket_name=f"cert-ra-logs-{env_config.env}",
                encryption_key=self.s3_cmk.key,  # pyright: ignore[reportArgumentType]
                lifecycle_rules=[
                    s3.LifecycleRule(
                        id="ExpireOldVersions",
                        noncurrent_version_expiration=cdk.Duration.days(30),
                    ),
                    s3.LifecycleRule(
                        id="TransitionToGlacier",
                        transitions=[
                            s3.Transition(
                                storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                                transition_after=cdk.Duration.days(90),
                            ),
                        ],
                    ),
                ],
            ),
        )

        self.assets_bucket = EncryptedBucket(
            self,
            "AssetsBucket",
            props=EncryptedBucketProps(
                bucket_name=f"cert-ra-assets-{env_config.env}",
                encryption_key=self.s3_cmk.key,  # pyright: ignore[reportArgumentType]
                lifecycle_rules=[
                    s3.LifecycleRule(
                        id="ExpireOldVersions",
                        noncurrent_version_expiration=cdk.Duration.days(30),
                    ),
                ],
            ),
        )

        # Outputs for downstream stacks + operator scripts.
        cdk.CfnOutput(
            self,
            "RdsCmkArn",
            value=self.rds_cmk.key.key_arn,
            export_name=f"{self.stack_name}-RdsCmkArn",
        )
        cdk.CfnOutput(
            self,
            "S3CmkArn",
            value=self.s3_cmk.key.key_arn,
            export_name=f"{self.stack_name}-S3CmkArn",
        )
        cdk.CfnOutput(
            self,
            "PostgresEndpoint",
            value=self.postgres.endpoint_address,
            export_name=f"{self.stack_name}-PostgresEndpoint",
        )
        cdk.CfnOutput(
            self,
            "PostgresMasterSecretArn",
            value=self.postgres.master_secret_arn,
            export_name=f"{self.stack_name}-PostgresMasterSecretArn",
        )
        cdk.CfnOutput(
            self,
            "LogsBucketName",
            value=self.logs_bucket.bucket_name,
            export_name=f"{self.stack_name}-LogsBucketName",
        )
        cdk.CfnOutput(
            self,
            "AssetsBucketName",
            value=self.assets_bucket.bucket_name,
            export_name=f"{self.stack_name}-AssetsBucketName",
        )
