# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from cdk_nag import NagSuppressions
from constructs import Construct


@dataclass(frozen=True, slots=True)
class EncryptedBucketProps:
    """Props for EncryptedBucket. See § Resource ownership matrix in the
    design spec — DataStack instantiates this twice: `cert-ra-logs-{env}`
    and `cert-ra-assets-{env}`."""

    bucket_name: str
    """Full bucket name. Must be globally unique across AWS."""

    encryption_key: kms.IKey
    """`cert-ra-s3-cmk` from DataStack."""

    lifecycle_rules: list[s3.LifecycleRule] | None = None
    """Optional bucket lifecycle rules. Caller controls retention."""

    enable_versioning: bool = True
    """Keep prior object versions; required for L2 tamper-resistance once
    Object Lock is added on the logs bucket."""


class EncryptedBucket(Construct):
    """S3 bucket with KMS encryption, versioning, public-access blocked,
    and a bucket policy denying non-TLS access.

    Server access logs are NOT enabled here — that requires a destination
    bucket and creates a chicken-and-egg with the logs bucket itself.
    `BaselineCloudTrail` (ObservabilityStack) is the canonical access-trail
    source.
    """

    bucket: s3.Bucket

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: EncryptedBucketProps,
    ) -> None:
        super().__init__(scope, construct_id)

        self.bucket = s3.Bucket(
            self,
            "Bucket",
            bucket_name=props.bucket_name,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=props.encryption_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=props.enable_versioning,
            lifecycle_rules=list(props.lifecycle_rules)
            if props.lifecycle_rules
            else None,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
        )

        # Deny cross-account principals as a belt-and-suspenders default.
        # The bucket policy + Block Public Access already prevent
        # external access, but this fails any cross-account assumed-role
        # request even if a future bucket policy ever opens up.
        account = cdk.Aws.ACCOUNT_ID
        self.bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyCrossAccountPrincipals",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],  # pyright: ignore[reportArgumentType]
                actions=["s3:*"],
                resources=[
                    self.bucket.bucket_arn,
                    f"{self.bucket.bucket_arn}/*",
                ],
                conditions={
                    "StringNotEquals": {"aws:PrincipalAccount": account},
                    "Bool": {"aws:PrincipalIsAWSService": "false"},
                },
            )
        )

        # Server access logging is not enabled here (see class docstring).
        NagSuppressions.add_resource_suppressions(
            self.bucket,
            [
                {
                    "id": "AwsSolutions-S1",
                    "reason": (
                        "Server access logs not enabled at this layer. CloudTrail "
                        "(ObservabilityStack BaselineCloudTrail) is the canonical "
                        "S3 access trail."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-S3BucketLoggingEnabled",
                    "reason": "Same as AwsSolutions-S1.",
                },
                {
                    "id": "NIST.800.53.R5-S3BucketReplicationEnabled",
                    "reason": (
                        "Cross-Region Replication is not in scope. Both buckets "
                        "(`cert-ra-logs-{env}` and `cert-ra-assets-{env}`) are "
                        "regional by design. If the audit later requires DR, "
                        "tracked as a future L-series item."
                    ),
                },
            ],
        )

    @property
    def bucket_arn(self) -> str:
        return self.bucket.bucket_arn

    @property
    def bucket_name(self) -> str:
        return self.bucket.bucket_name
