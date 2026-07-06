# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.data.cmk import NarrowKmsCmk, NarrowKmsCmkProps
from cert_ra_infra.constructs.data.encrypted_bucket import (
    EncryptedBucket,
    EncryptedBucketProps,
)


def _synth(
    *,
    bucket_name: str = "cert-ra-logs-staging",
    enable_versioning: bool = True,
) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    cmk = NarrowKmsCmk(
        stack,
        "S3Cmk",
        props=NarrowKmsCmkProps(
            key_id="s3",
            env="test",
            purpose="encrypt",
            service_principals=["s3.amazonaws.com"],
        ),
    )
    EncryptedBucket(
        stack,
        "Bucket",
        props=EncryptedBucketProps(
            bucket_name=bucket_name,
            encryption_key=cmk.key,  # pyright: ignore[reportArgumentType]
            enable_versioning=enable_versioning,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_bucket_name_is_set() -> None:
    template = _synth(bucket_name="cert-ra-assets-prod")
    template.has_resource_properties(
        "AWS::S3::Bucket", {"BucketName": "cert-ra-assets-prod"}
    )


def test_bucket_blocks_public_access() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        },
    )


def test_bucket_uses_kms_encryption_with_provided_cmk() -> None:
    template = _synth()
    buckets = template.find_resources("AWS::S3::Bucket")
    (bucket,) = buckets.values()
    encryption = bucket["Properties"]["BucketEncryption"]
    rules = encryption["ServerSideEncryptionConfiguration"]
    sse = rules[0]["ServerSideEncryptionByDefault"]
    assert sse["SSEAlgorithm"] == "aws:kms"
    assert "KMSMasterKeyID" in sse


def test_versioning_is_on_by_default() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::S3::Bucket", {"VersioningConfiguration": {"Status": "Enabled"}}
    )


def test_versioning_can_be_disabled() -> None:
    template = _synth(enable_versioning=False)
    buckets = template.find_resources("AWS::S3::Bucket")
    (bucket,) = buckets.values()
    # CDK omits VersioningConfiguration entirely when disabled
    assert (
        bucket["Properties"].get("VersioningConfiguration", {}).get("Status")
        != "Enabled"
    )


def test_bucket_enforces_ssl() -> None:
    """The bucket policy must deny non-TLS requests (enforce_ssl=True)."""
    template = _synth()
    policies = template.find_resources("AWS::S3::BucketPolicy")
    (policy,) = policies.values()
    statements = policy["Properties"]["PolicyDocument"]["Statement"]
    deny_non_tls = [
        s
        for s in statements
        if s.get("Effect") == "Deny"
        and "SecureTransport" in str(s.get("Condition", {}).get("Bool", {}))
    ]
    assert len(deny_non_tls) >= 1, "Missing deny-non-TLS statement"


def test_bucket_policy_denies_cross_account_principals() -> None:
    template = _synth()
    policies = template.find_resources("AWS::S3::BucketPolicy")
    (policy,) = policies.values()
    statements = policy["Properties"]["PolicyDocument"]["Statement"]
    cross_account_deny = [
        s for s in statements if s.get("Sid") == "DenyCrossAccountPrincipals"
    ]
    assert len(cross_account_deny) == 1


def test_bucket_owner_enforced() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "OwnershipControls": {
                "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}],
            },
        },
    )


def test_bucket_retains_on_stack_delete() -> None:
    template = _synth()
    buckets = template.find_resources("AWS::S3::Bucket")
    (bucket,) = buckets.values()
    assert bucket.get("DeletionPolicy") == "Retain"
