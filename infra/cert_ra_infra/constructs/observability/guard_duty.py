# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

from aws_cdk import aws_guardduty as guardduty
from constructs import Construct


@dataclass(frozen=True, slots=True)
class BaselineGuardDutyProps:
    """Props for BaselineGuardDuty. See § Audit & detection in the design spec."""

    finding_publishing_frequency: str = "FIFTEEN_MINUTES"
    """How often findings are published to EventBridge / S3. Options:
    `FIFTEEN_MINUTES`, `ONE_HOUR`, `SIX_HOURS`. Default 15 minutes for fast
    incident response."""

    enable_default_detector: bool = True
    """Day-one config: enable the default detector. L5 hardens by enabling
    the opt-in protections (S3, RDS Login Activity, Malware Protection for
    ECS, Runtime Monitoring for ECS)."""


class BaselineGuardDuty(Construct):
    """GuardDuty detector with default settings.

    L5 (tracked) adds:
    - S3 Protection
    - RDS Login Activity Monitoring
    - Malware Protection for ECS
    - Runtime Monitoring for ECS

    Runtime Monitoring in particular would catch the H2 exfil scenario at
    the syscall level inside the maintenance container.
    """

    detector: guardduty.CfnDetector

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: BaselineGuardDutyProps,
    ) -> None:
        super().__init__(scope, construct_id)

        self.detector = guardduty.CfnDetector(
            self,
            "Detector",
            enable=props.enable_default_detector,
            finding_publishing_frequency=props.finding_publishing_frequency,
        )

    @property
    def detector_id(self) -> str:
        return self.detector.attr_id
