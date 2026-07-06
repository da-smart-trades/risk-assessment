# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.temporal.cert_issuance import (
    InitialCertIssuance,
    InitialCertIssuanceProps,
    TemporalServiceCertConfig,
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
    InitialCertIssuance(
        stack,
        "Issuance",
        props=InitialCertIssuanceProps(
            subordinate_ca_arn=_SUBORDINATE_CA_ARN,
            services=services,
            secrets_cmk_arn=_SECRETS_CMK_ARN,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_handler_lambda_is_python_312() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.12", "Handler": "handler.handler"},
    )


def test_handler_has_acm_pca_issue_certificate_permission_scoped_to_ca() -> None:
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    matching: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "IssueCertsAgainstSubordinate":
                matching.append(stmt)
    assert len(matching) == 1
    stmt = matching[0]
    actions = stmt["Action"]
    action_set: set[object] = (
        set(actions) if isinstance(actions, list) else {actions}  # pyright: ignore[reportUnknownArgumentType]
    )
    assert action_set == {"acm-pca:IssueCertificate", "acm-pca:GetCertificate"}
    assert stmt["Resource"] == _SUBORDINATE_CA_ARN


def test_handler_has_secret_read_write_scoped_to_target_secrets() -> None:
    """The handler needs Get + Put on each target secret. Get is used on
    the Update path to detect which services are still unpopulated;
    Put is used to write fresh cert payloads on Create + Update."""
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    matching: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "ReadAndWriteTemporalMtlsSecrets":
                matching.append(stmt)
    assert len(matching) == 1
    stmt = matching[0]
    assert set(stmt["Action"]) == {
        "secretsmanager:GetSecretValue",
        "secretsmanager:PutSecretValue",
    }
    resources = stmt["Resource"]
    assert isinstance(resources, list)
    assert len(resources) == 5  # pyright: ignore[reportUnknownArgumentType]


def test_handler_has_kms_encrypt_on_secrets_cmk() -> None:
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    matching: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "EncryptViaSecretsCmk":
                matching.append(stmt)
    assert len(matching) == 1
    stmt = matching[0]
    assert stmt["Resource"] == _SECRETS_CMK_ARN
    from typing import cast

    actions = cast(list[str], stmt["Action"])
    assert "kms:Encrypt" in actions
    assert "kms:GenerateDataKey*" in actions


def test_handler_has_15_minute_timeout() -> None:
    """ACM PCA issuance can take a minute or two per cert; 15 min covers all 5."""
    template = _synth()
    template.has_resource_properties("AWS::Lambda::Function", {"Timeout": 900})


def test_custom_resource_uses_namespaced_resource_type() -> None:
    template = _synth()
    template.resource_count_is("Custom::TemporalMtlsInitialCertIssuance", 1)


def test_custom_resource_passes_subordinate_ca_arn() -> None:
    template = _synth()
    crs = template.find_resources("Custom::TemporalMtlsInitialCertIssuance")
    (cr,) = crs.values()
    assert cr["Properties"]["SubordinateCaArn"] == _SUBORDINATE_CA_ARN


def test_custom_resource_passes_all_five_services() -> None:
    template = _synth()
    crs = template.find_resources("Custom::TemporalMtlsInitialCertIssuance")
    (cr,) = crs.values()
    services = cr["Properties"]["Services"]
    assert {svc["Name"] for svc in services} == set(_SERVICE_NAMES)


def test_validity_days_defaults_to_397() -> None:
    """A6: 13-month validity, auto-renewed at 60% of lifetime by ACM PCA."""
    template = _synth()
    crs = template.find_resources("Custom::TemporalMtlsInitialCertIssuance")
    (cr,) = crs.values()
    for svc in cr["Properties"]["Services"]:
        assert svc["ValidityDays"] == 397


def test_provider_framework_lambda_present() -> None:
    """The CDK custom-resources Provider creates a framework Lambda in addition to ours."""
    template = _synth()
    functions = template.find_resources("AWS::Lambda::Function")
    assert len(functions) >= 2
