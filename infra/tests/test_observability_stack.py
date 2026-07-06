# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.stacks._config import load_env
from cert_ra_infra.stacks.data import DataStack, DataStackProps
from cert_ra_infra.stacks.network import NetworkStack
from cert_ra_infra.stacks.observability import (
    ObservabilityStack,
    ObservabilityStackProps,
)

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
    data = DataStack(
        app,
        f"CertRa-DataStack-{cfg.env}",
        env=env,
        env_config=cfg,
        data_props=DataStackProps(
            network=network, installer_role_arn_pattern=_INSTALLER_ARN
        ),
    )
    stack = ObservabilityStack(
        app,
        f"CertRa-ObservabilityStack-{cfg.env}",
        env=env,
        env_config=cfg,
        observability_props=ObservabilityStackProps(
            data=data, installer_role_arn_pattern=_INSTALLER_ARN
        ),
    )
    return assertions.Template.from_stack(stack)


def test_stack_creates_logs_cmk_with_correct_alias() -> None:
    template = _synth_stack()
    template.has_resource_properties(
        "AWS::KMS::Alias", {"AliasName": "alias/cert-ra-logs-staging"}
    )


def test_stack_creates_cloud_trail() -> None:
    template = _synth_stack()
    template.resource_count_is("AWS::CloudTrail::Trail", 1)
    template.has_resource_properties(
        "AWS::CloudTrail::Trail",
        {"IsMultiRegionTrail": True, "EnableLogFileValidation": True},
    )


def test_staging_trail_uses_staging_name() -> None:
    template = _synth_stack("staging")
    template.has_resource_properties(
        "AWS::CloudTrail::Trail",
        {"TrailName": "cert-ra-trail-staging"},
    )


def test_prod_trail_uses_prod_name() -> None:
    template = _synth_stack("prod")
    template.has_resource_properties(
        "AWS::CloudTrail::Trail",
        {"TrailName": "cert-ra-trail-prod"},
    )


def test_stack_creates_guard_duty_detector() -> None:
    template = _synth_stack()
    template.resource_count_is("AWS::GuardDuty::Detector", 1)
    template.has_resource_properties(
        "AWS::GuardDuty::Detector",
        {"Enable": True, "FindingPublishingFrequency": "FIFTEEN_MINUTES"},
    )


def test_logs_cmk_lists_cloudtrail_service_principal() -> None:
    """The logs CMK must allow cloudtrail.amazonaws.com to encrypt trail events."""
    template = _synth_stack()
    keys = template.find_resources("AWS::KMS::Key")
    cloud_trail_present = False
    for key in keys.values():
        statements = key["Properties"]["KeyPolicy"]["Statement"]
        for stmt in statements:
            if stmt.get("Sid") == "ServicePrincipalUse":
                principals = stmt.get("Principal", {})
                services = principals.get("Service", [])
                if isinstance(services, str):
                    services = [services]
                if "cloudtrail.amazonaws.com" in services:
                    cloud_trail_present = True
    assert cloud_trail_present, "logs-cmk must allow cloudtrail.amazonaws.com"


def test_logs_cmk_lists_region_specific_logs_service_principal() -> None:
    """`logs.<region>.amazonaws.com` is the region-specific form AWS requires.

    Derived from the env's configured region (not hardcoded) so it tracks
    `EnvConfig.region` — both envs deploy to us-east-2."""
    expected = f"logs.{load_env('staging').region}.amazonaws.com"
    template = _synth_stack("staging")
    keys = template.find_resources("AWS::KMS::Key")
    logs_present = False
    for key in keys.values():
        for stmt in key["Properties"]["KeyPolicy"]["Statement"]:
            if stmt.get("Sid") == "ServicePrincipalUse":
                principals = stmt.get("Principal", {})
                services = principals.get("Service", [])
                if isinstance(services, str):
                    services = [services]
                if expected in services:
                    logs_present = True
    assert logs_present, f"logs-cmk must allow {expected}"


def test_stack_exports_required_outputs() -> None:
    template = _synth_stack()
    outputs = template.find_outputs("*")
    required = {
        "LogsCmkArn",
        "CloudTrailArn",
        "CloudTrailLogGroupArn",
        "GuardDutyDetectorId",
    }
    assert required.issubset(set(outputs.keys()))
