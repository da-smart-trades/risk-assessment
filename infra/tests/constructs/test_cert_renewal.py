# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import cast

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.temporal.cert_issuance import (
    TemporalServiceCertConfig,
)
from cert_ra_infra.constructs.temporal.cert_renewal import (
    CertRenewal,
    CertRenewalProps,
)

_SUBORDINATE_CA_ARN = (
    "arn:aws:acm-pca:us-east-1:111111111111:"
    "certificate-authority/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
)
_SECRETS_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/00000000-1111-2222-3333-444444444444"
)
_SERVICE_NAMES = (
    "temporal-frontend",
    "worker-metrics",
    "worker-alerts",
    "internal-worker",
    "maint",
)


def _synth() -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    services = [
        TemporalServiceCertConfig(
            name=name,
            secret_arn=(
                f"arn:aws:secretsmanager:us-east-1:111111111111:"
                f"secret:/cert-ra/staging/temporal/mtls/{name}-AbCdEf"
            ),
            secret_name=f"/cert-ra/staging/temporal/mtls/{name}",
            common_name=f"{name}.cert-ra.local",
        )
        for name in _SERVICE_NAMES
    ]
    CertRenewal(
        stack,
        "Renewal",
        props=CertRenewalProps(
            subordinate_ca_arn=_SUBORDINATE_CA_ARN,
            services=services,
            secrets_cmk_arn=_SECRETS_CMK_ARN,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_python_312_lambda_with_handler_entrypoint() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.12", "Handler": "handler.handler"},
    )


def test_lambda_has_pca_issue_permission_scoped_to_subordinate() -> None:
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    matching: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "IssueCertsAgainstSubordinate":
                matching.append(stmt)
    assert len(matching) == 1
    stmt = matching[0]
    actions: set[object] = (
        set(stmt["Action"])  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(stmt["Action"], list)
        else {stmt["Action"]}
    )
    assert actions == {"acm-pca:IssueCertificate", "acm-pca:GetCertificate"}
    assert stmt["Resource"] == _SUBORDINATE_CA_ARN


def test_lambda_has_both_get_and_put_secret_value() -> None:
    """Renewal needs Get (to read existing cert + parse expiration) and
    Put (to overwrite with the renewed payload)."""
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    matching: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "ReadAndWriteTemporalMtlsSecrets":
                matching.append(stmt)
    assert len(matching) == 1
    stmt = matching[0]
    actions: set[object] = (
        set(stmt["Action"])  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(stmt["Action"], list)
        else {stmt["Action"]}
    )
    assert actions == {
        "secretsmanager:GetSecretValue",
        "secretsmanager:PutSecretValue",
    }
    resources = stmt["Resource"]
    assert isinstance(resources, list)
    assert len(resources) == 5  # pyright: ignore[reportUnknownArgumentType]


def test_lambda_has_kms_encrypt_decrypt_on_secrets_cmk() -> None:
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    matching: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "UseSecretsCmk":
                matching.append(stmt)
    assert len(matching) == 1
    stmt = matching[0]
    assert stmt["Resource"] == _SECRETS_CMK_ARN
    actions = cast(list[str], stmt["Action"])
    assert "kms:Encrypt" in actions
    assert "kms:Decrypt" in actions  # need both directions for renewal


def test_lambda_has_15_minute_timeout() -> None:
    template = _synth()
    template.has_resource_properties("AWS::Lambda::Function", {"Timeout": 900})


def test_eventbridge_rule_is_created() -> None:
    template = _synth()
    template.resource_count_is("AWS::Events::Rule", 1)


def test_default_schedule_is_daily_at_02_00_utc() -> None:
    template = _synth()
    rules = template.find_resources("AWS::Events::Rule")
    (rule,) = rules.values()
    # CDK emits cron expressions as `cron(min hour day month ? year)` style
    # — accept either a cron(...) or a rate(...) form, but verify it's daily
    schedule = rule["Properties"]["ScheduleExpression"]
    assert schedule.startswith("cron("), f"Expected a cron schedule, got: {schedule}"
    # The minute should be 0 and hour should be 2 (matching our default)
    assert "0 2" in schedule, f"Expected daily 02:00 UTC, got: {schedule}"


def test_rule_targets_the_handler_lambda_with_event_payload() -> None:
    template = _synth()
    rules = template.find_resources("AWS::Events::Rule")
    (rule,) = rules.values()
    targets = rule["Properties"]["Targets"]
    assert len(targets) == 1
    target = targets[0]
    # The target's Input field should carry the JSON-encoded event payload
    assert "Input" in target
    assert _SUBORDINATE_CA_ARN in target["Input"]


def test_event_payload_includes_all_five_services() -> None:
    template = _synth()
    rules = template.find_resources("AWS::Events::Rule")
    (rule,) = rules.values()
    target_input = rule["Properties"]["Targets"][0]["Input"]
    # All five service names should appear in the JSON-encoded input
    for name in _SERVICE_NAMES:
        assert name in target_input, f"Service {name} missing from rule input"


def test_event_payload_includes_renew_threshold() -> None:
    template = _synth()
    rules = template.find_resources("AWS::Events::Rule")
    (rule,) = rules.values()
    target_input = rule["Properties"]["Targets"][0]["Input"]
    assert "RenewWhenDaysRemaining" in target_input
    assert "159" in target_input  # default 40% of 397
