# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_s3 as s3

from cert_ra_infra.constructs.data.cmk import NarrowKmsCmk, NarrowKmsCmkProps
from cert_ra_infra.constructs.observability.cloud_trail import (
    BaselineCloudTrail,
    BaselineCloudTrailProps,
)


def _synth(*, trail_name: str = "cert-ra-trail-staging") -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    cmk = NarrowKmsCmk(
        stack,
        "LogsCmk",
        props=NarrowKmsCmkProps(
            key_id="logs",
            env="test",
            purpose="encrypt",
            service_principals=[
                "logs.us-east-1.amazonaws.com",
                "cloudtrail.amazonaws.com",
            ],
        ),
    )
    bucket = s3.Bucket(
        stack,
        "LogsBucket",
        encryption=s3.BucketEncryption.S3_MANAGED,
        removal_policy=cdk.RemovalPolicy.RETAIN,
    )
    BaselineCloudTrail(
        stack,
        "Trail",
        props=BaselineCloudTrailProps(
            trail_name=trail_name,
            log_bucket=bucket,
            encryption_key=cmk.key,  # pyright: ignore[reportArgumentType]
        ),
    )
    return assertions.Template.from_stack(stack)


def test_trail_is_multi_region() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::CloudTrail::Trail", {"IsMultiRegionTrail": True}
    )


def test_trail_includes_global_service_events() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::CloudTrail::Trail", {"IncludeGlobalServiceEvents": True}
    )


def test_trail_has_file_validation_enabled() -> None:
    """CloudTrail digest files allow tamper detection on the S3 store."""
    template = _synth()
    template.has_resource_properties(
        "AWS::CloudTrail::Trail", {"EnableLogFileValidation": True}
    )


def test_trail_is_kms_encrypted() -> None:
    template = _synth()
    trails = template.find_resources("AWS::CloudTrail::Trail")
    (trail,) = trails.values()
    assert "KMSKeyId" in trail["Properties"]


def test_trail_name_is_set() -> None:
    template = _synth(trail_name="cert-ra-trail-prod")
    template.has_resource_properties(
        "AWS::CloudTrail::Trail", {"TrailName": "cert-ra-trail-prod"}
    )


def test_trail_writes_to_provided_bucket() -> None:
    template = _synth()
    trails = template.find_resources("AWS::CloudTrail::Trail")
    (trail,) = trails.values()
    assert "S3BucketName" in trail["Properties"]


def test_trail_writes_to_cloudwatch_logs() -> None:
    """Dual-write to CW Logs for ad-hoc query access."""
    template = _synth()
    trails = template.find_resources("AWS::CloudTrail::Trail")
    (trail,) = trails.values()
    assert "CloudWatchLogsLogGroupArn" in trail["Properties"]
    assert "CloudWatchLogsRoleArn" in trail["Properties"]


def test_log_group_uses_kms_encryption() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::Logs::LogGroup",
        assertions.Match.object_like({"KmsKeyId": assertions.Match.any_value()}),
    )


def test_log_group_retention_is_three_months() -> None:
    """90-day retention (THREE_MONTHS) for the dual-write CW group."""
    template = _synth()
    template.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": 90})


def test_log_group_retains_on_stack_delete() -> None:
    template = _synth()
    log_groups = template.find_resources("AWS::Logs::LogGroup")
    (log_group,) = log_groups.values()
    assert log_group.get("DeletionPolicy") == "Retain"


def test_management_events_are_recorded_as_all() -> None:
    """Both read and write management events captured."""
    template = _synth()
    trails = template.find_resources("AWS::CloudTrail::Trail")
    (trail,) = trails.values()
    selectors = trail["Properties"].get("EventSelectors", [])
    assert selectors, "Expected at least one event selector"
    assert any(s.get("ReadWriteType") == "All" for s in selectors), (
        "Expected ReadWriteType=All for management events"
    )
