# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.observability.guard_duty import (
    BaselineGuardDuty,
    BaselineGuardDutyProps,
)


def _synth(
    *,
    enable_default_detector: bool = True,
    finding_publishing_frequency: str = "FIFTEEN_MINUTES",
) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    BaselineGuardDuty(
        stack,
        "GuardDuty",
        props=BaselineGuardDutyProps(
            enable_default_detector=enable_default_detector,
            finding_publishing_frequency=finding_publishing_frequency,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_detector_is_created() -> None:
    template = _synth()
    template.resource_count_is("AWS::GuardDuty::Detector", 1)


def test_detector_is_enabled_by_default() -> None:
    template = _synth()
    template.has_resource_properties("AWS::GuardDuty::Detector", {"Enable": True})


def test_detector_can_be_disabled() -> None:
    template = _synth(enable_default_detector=False)
    template.has_resource_properties("AWS::GuardDuty::Detector", {"Enable": False})


def test_finding_publishing_frequency_defaults_to_fifteen_minutes() -> None:
    """15-min publishing for fast incident response."""
    template = _synth()
    template.has_resource_properties(
        "AWS::GuardDuty::Detector",
        {"FindingPublishingFrequency": "FIFTEEN_MINUTES"},
    )


def test_finding_publishing_frequency_is_configurable() -> None:
    template = _synth(finding_publishing_frequency="ONE_HOUR")
    template.has_resource_properties(
        "AWS::GuardDuty::Detector",
        {"FindingPublishingFrequency": "ONE_HOUR"},
    )
