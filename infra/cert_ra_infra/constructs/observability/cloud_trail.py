# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_cloudtrail as cloudtrail
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from cdk_nag import NagSuppressions
from constructs import Construct


@dataclass(frozen=True, slots=True)
class BaselineCloudTrailProps:
    """Props for BaselineCloudTrail. See § Audit & detection in the design spec."""

    trail_name: str
    """e.g. `cert-ra-trail-{env}`."""

    log_bucket: s3.IBucket
    """Destination S3 bucket for the trail (typically DataStack's `cert-ra-logs-{env}`)."""

    encryption_key: kms.IKey
    """`cert-ra-logs-cmk` for trail event encryption."""

    # CloudWatch Logs retention is hardcoded to THREE_MONTHS (90 days) — the
    # CW side is for ad-hoc query access; the canonical retained store is the
    # S3 bucket. L2 hardens the S3 side to Object Lock; CW retention isn't
    # security-load-bearing.


class BaselineCloudTrail(Construct):
    """Account-wide multi-region CloudTrail.

    Day-one configuration:
    - Multi-region (captures activity in every region)
    - KMS-encrypted trail events (`cert-ra-logs-cmk`)
    - File integrity validation enabled (CloudTrail digest files)
    - Dual-write to S3 (DataStack's logs bucket) + a CloudWatch Logs
      group for ad-hoc query
    - Management events: All (read + write)
    - S3 data events deferred (high volume; L2 enables them on
      sensitive prefixes only)

    L2 (tracked) hardens to Object Lock compliance mode on the S3 bucket
    + bucket policy denying `s3:DeleteObject*` for all principals
    including root.
    """

    trail: cloudtrail.Trail
    log_group: logs.LogGroup

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: BaselineCloudTrailProps,
    ) -> None:
        super().__init__(scope, construct_id)

        self.log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=f"/aws/cloudtrail/{props.trail_name}",
            retention=logs.RetentionDays.THREE_MONTHS,
            encryption_key=props.encryption_key,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        self.trail = cloudtrail.Trail(
            self,
            "Trail",
            trail_name=props.trail_name,
            bucket=props.log_bucket,
            encryption_key=props.encryption_key,
            is_multi_region_trail=True,
            include_global_service_events=True,
            enable_file_validation=True,
            send_to_cloud_watch_logs=True,
            cloud_watch_log_group=self.log_group,
            # management_events intentionally unset — see the EventSelectors
            # deletion override below.
        )

        # CDK's L2 Trail always renders an `EventSelectors` block (even an
        # empty one), and CloudFormation applies it via
        # cloudtrail:PutEventSelectors — an action the cfn-exec permissions
        # boundary (DenyAuditTrailDestruction) deliberately denies, so the
        # stack create fails with AccessDenied. Strip the property entirely:
        # CloudFormation then only calls CreateTrail, and a trail with NO
        # event selectors logs ALL management events (read + write) by
        # CloudTrail default — identical coverage to management_events=ALL —
        # so the audit posture is unchanged and the boundary stays intact.
        cfn_trail = self.trail.node.default_child
        assert isinstance(cfn_trail, cloudtrail.CfnTrail)
        cfn_trail.add_property_deletion_override("EventSelectors")

        # RETAIN the trail: the cfn-exec permissions boundary denies
        # cloudtrail:DeleteTrail / StopLogging (anti audit-trail-destruction),
        # so CloudFormation can never delete it — without RETAIN, any stack
        # rollback/delete fails (DELETE_FAILED → ROLLBACK_FAILED) trying to.
        # Matches the log group's RETAIN above and the boundary's intent that
        # audit trails are never torn down by automation.
        cfn_trail.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        NagSuppressions.add_resource_suppressions(
            self.trail,
            [
                {
                    "id": "AwsSolutions-S1",
                    "reason": (
                        "S3 access logs for the CloudTrail destination bucket are "
                        "out of scope; CloudTrail itself is the canonical audit "
                        "trail for that bucket."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                    "reason": (
                        "CDK auto-creates a CloudTrail → CloudWatch Logs delivery "
                        "role with an inline policy when send_to_cloud_watch_logs=True. "
                        "We don't author the policy; replacing it with a managed "
                        "policy would require dropping to L1 constructs."
                    ),
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "CW Logs delivery role uses logs:CreateLogStream + PutLogEvents "
                        "on the destination log group with log-stream-name wildcards "
                        "(stream names aren't pre-computable)."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @property
    def trail_arn(self) -> str:
        return self.trail.trail_arn

    @property
    def log_group_arn(self) -> str:
        return self.log_group.log_group_arn
