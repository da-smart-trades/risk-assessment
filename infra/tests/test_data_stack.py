# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.stacks._config import load_env
from cert_ra_infra.stacks.data import DataStack, DataStackProps
from cert_ra_infra.stacks.network import NetworkStack

_INSTALLER_ARN = (
    "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/"
    "AWSReservedSSO_CertRaInstaller_*"
)


def _synth_stack(env_name: str = "staging") -> assertions.Template:
    app = cdk.App()
    cfg = load_env(env_name)
    env = cdk.Environment(account="111111111111", region=cfg.region)
    network = NetworkStack(
        app, f"CertRa-NetworkStack-{cfg.env}", env=env, env_config=cfg
    )
    stack = DataStack(
        app,
        f"CertRa-DataStack-{cfg.env}",
        env=env,
        env_config=cfg,
        data_props=DataStackProps(
            network=network, installer_role_arn_pattern=_INSTALLER_ARN
        ),
    )
    return assertions.Template.from_stack(stack)


def test_stack_creates_rds_cmk_with_correct_alias() -> None:
    template = _synth_stack()
    template.has_resource_properties(
        "AWS::KMS::Alias", {"AliasName": "alias/cert-ra-rds-staging"}
    )


def test_stack_creates_s3_cmk_with_correct_alias() -> None:
    template = _synth_stack()
    template.has_resource_properties(
        "AWS::KMS::Alias", {"AliasName": "alias/cert-ra-s3-staging"}
    )


def test_stack_creates_two_cmks_total() -> None:
    template = _synth_stack()
    template.resource_count_is("AWS::KMS::Key", 2)


def test_stack_creates_multi_az_postgres() -> None:
    template = _synth_stack()
    template.resource_count_is("AWS::RDS::DBInstance", 1)
    template.has_resource_properties("AWS::RDS::DBInstance", {"MultiAZ": True})


def test_stack_creates_logs_and_assets_buckets() -> None:
    template = _synth_stack("staging")
    template.resource_count_is("AWS::S3::Bucket", 2)
    template.has_resource_properties(
        "AWS::S3::Bucket", {"BucketName": "cert-ra-logs-staging"}
    )
    template.has_resource_properties(
        "AWS::S3::Bucket", {"BucketName": "cert-ra-assets-staging"}
    )


def test_prod_buckets_use_prod_naming() -> None:
    template = _synth_stack("prod")
    template.has_resource_properties(
        "AWS::S3::Bucket", {"BucketName": "cert-ra-logs-prod"}
    )
    template.has_resource_properties(
        "AWS::S3::Bucket", {"BucketName": "cert-ra-assets-prod"}
    )


def test_logs_bucket_has_glacier_lifecycle_transition() -> None:
    """Logs bucket archives to Glacier Instant Retrieval after 90 days."""
    template = _synth_stack("staging")
    buckets = template.find_resources(
        "AWS::S3::Bucket",
        {"Properties": {"BucketName": "cert-ra-logs-staging"}},
    )
    (logs_bucket,) = buckets.values()
    lifecycle = logs_bucket["Properties"].get("LifecycleConfiguration", {})
    rules = lifecycle.get("Rules", [])
    glacier_rules = [
        r
        for r in rules
        if any(t.get("StorageClass") == "GLACIER_IR" for t in r.get("Transitions", []))
    ]
    assert len(glacier_rules) == 1


def test_stack_exports_required_outputs() -> None:
    template = _synth_stack()
    outputs = template.find_outputs("*")
    required = {
        "RdsCmkArn",
        "S3CmkArn",
        "PostgresEndpoint",
        "PostgresMasterSecretArn",
        "LogsBucketName",
        "AssetsBucketName",
    }
    assert required.issubset(set(outputs.keys()))
