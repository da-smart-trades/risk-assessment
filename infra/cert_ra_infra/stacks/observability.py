# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack
from constructs import Construct

from cert_ra_infra.constructs.data.cmk import NarrowKmsCmk, NarrowKmsCmkProps
from cert_ra_infra.constructs.observability.cloud_trail import (
    BaselineCloudTrail,
    BaselineCloudTrailProps,
)
from cert_ra_infra.constructs.observability.guard_duty import (
    BaselineGuardDuty,
    BaselineGuardDutyProps,
)
from cert_ra_infra.stacks._config import EnvConfig
from cert_ra_infra.stacks.data import DataStack


@dataclass(frozen=True, slots=True)
class ObservabilityStackProps:
    """Stack-level inputs for ObservabilityStack.

    Wired from outside via cross-stack reference: the `data` arg pulls the
    `cert-ra-logs-{env}` S3 bucket from DataStack (CloudTrail's
    destination). `installer_role_arn_pattern` matches the IAM Identity
    Center-provisioned Installer roles for use in CMK admin policies.
    """

    data: DataStack
    installer_role_arn_pattern: str


class ObservabilityStack(Stack):
    """Foundation observability — `cert-ra-logs-cmk`, multi-region
    CloudTrail, and a baseline GuardDuty detector.

    Per the resource ownership matrix, this stack owns:
    - `cert-ra-logs-cmk` (CloudWatch Logs + CloudTrail encryption)
    - `BaselineCloudTrail` (multi-region, KMS-encrypted, dual-write S3 +
      CW Logs, file integrity validation)
    - `BaselineGuardDuty` (default detector; L5 adds opt-in protections)

    Deferred to follow-up PRs:
    - `BaselineConfigRules` — AWS Config requires an account-wide
      recorder + delivery channel setup that has a first-deploy chicken-
      and-egg with the logs bucket. Tracked as L6 in the security
      backlog.
    - `ServiceDashboard` — per-service CW dashboards land alongside the
      services that need them.
    - `AdotCollectorSidecar` — Temporal Prometheus → CloudWatch bridge
      lands with TemporalStack.
    """

    logs_cmk: NarrowKmsCmk
    cloud_trail: BaselineCloudTrail
    guard_duty: BaselineGuardDuty

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_config: EnvConfig,
        observability_props: ObservabilityStackProps,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_config = env_config

        # `logs.<region>.amazonaws.com` is the region-specific service
        # principal AWS requires for CloudWatch Logs KMS encryption. We hard-
        # code it from the env's region so the key policy is explicit.
        logs_service_principal = f"logs.{env_config.region}.amazonaws.com"

        self.logs_cmk = NarrowKmsCmk(
            self,
            "LogsCmk",
            props=NarrowKmsCmkProps(
                key_id="logs",
                env=env_config.env,
                purpose="encrypt",
                service_principals=[
                    logs_service_principal,
                    "cloudtrail.amazonaws.com",
                ],
                admin_roles=[observability_props.installer_role_arn_pattern],
            ),
        )

        self.cloud_trail = BaselineCloudTrail(
            self,
            "CloudTrail",
            props=BaselineCloudTrailProps(
                trail_name=f"cert-ra-trail-{env_config.env}",
                log_bucket=observability_props.data.logs_bucket.bucket,
                encryption_key=self.logs_cmk.key,  # pyright: ignore[reportArgumentType]
            ),
        )

        self.guard_duty = BaselineGuardDuty(
            self,
            "GuardDuty",
            props=BaselineGuardDutyProps(),
        )

        # Outputs.
        cdk.CfnOutput(
            self,
            "LogsCmkArn",
            value=self.logs_cmk.key.key_arn,
            export_name=f"{self.stack_name}-LogsCmkArn",
        )
        cdk.CfnOutput(
            self,
            "CloudTrailArn",
            value=self.cloud_trail.trail_arn,
            export_name=f"{self.stack_name}-CloudTrailArn",
        )
        cdk.CfnOutput(
            self,
            "CloudTrailLogGroupArn",
            value=self.cloud_trail.log_group_arn,
            export_name=f"{self.stack_name}-CloudTrailLogGroupArn",
        )
        cdk.CfnOutput(
            self,
            "GuardDutyDetectorId",
            value=self.guard_duty.detector_id,
            export_name=f"{self.stack_name}-GuardDutyDetectorId",
        )
