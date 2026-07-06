# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.dns.hosted_zone import (
    HostedZoneWithCert,
    HostedZoneWithCertProps,
)


def _synth(
    *,
    domain_name: str = "cert-ra.staging.certora.com",
    subject_alternative_names: list[str] | None = None,
    add_wildcard_san: bool = True,
) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    HostedZoneWithCert(
        stack,
        "Dns",
        props=HostedZoneWithCertProps(
            domain_name=domain_name,
            subject_alternative_names=subject_alternative_names or [],
            add_wildcard_san=add_wildcard_san,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_public_hosted_zone_for_domain() -> None:
    template = _synth(domain_name="cert-ra.staging.certora.com")
    template.has_resource_properties(
        "AWS::Route53::HostedZone",
        {"Name": "cert-ra.staging.certora.com."},
    )


def test_creates_acm_cert_for_primary_domain() -> None:
    template = _synth(domain_name="cert-ra.staging.certora.com")
    template.has_resource_properties(
        "AWS::CertificateManager::Certificate",
        {"DomainName": "cert-ra.staging.certora.com"},
    )


def test_certificate_uses_dns_validation() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::CertificateManager::Certificate",
        {"ValidationMethod": "DNS"},
    )


def test_wildcard_san_added_by_default() -> None:
    template = _synth(domain_name="cert-ra.prod.certora.com")
    certs = template.find_resources("AWS::CertificateManager::Certificate")
    (cert,) = certs.values()
    sans = cert["Properties"].get("SubjectAlternativeNames", [])
    assert "*.cert-ra.prod.certora.com" in sans


def test_wildcard_san_can_be_disabled() -> None:
    template = _synth(add_wildcard_san=False)
    certs = template.find_resources("AWS::CertificateManager::Certificate")
    (cert,) = certs.values()
    sans = cert["Properties"].get("SubjectAlternativeNames", [])
    assert sans == [] or sans is None


def test_explicit_sans_are_preserved() -> None:
    template = _synth(
        subject_alternative_names=["api.cert-ra.staging.certora.com"],
        add_wildcard_san=False,
    )
    certs = template.find_resources("AWS::CertificateManager::Certificate")
    (cert,) = certs.values()
    sans = cert["Properties"]["SubjectAlternativeNames"]
    assert "api.cert-ra.staging.certora.com" in sans


def test_wildcard_not_duplicated_if_already_in_sans() -> None:
    template = _synth(
        subject_alternative_names=["*.cert-ra.staging.certora.com"],
        add_wildcard_san=True,
    )
    certs = template.find_resources("AWS::CertificateManager::Certificate")
    (cert,) = certs.values()
    sans = cert["Properties"]["SubjectAlternativeNames"]
    assert sans.count("*.cert-ra.staging.certora.com") == 1


def test_certificate_dns_validation_references_hosted_zone() -> None:
    template = _synth()
    certs = template.find_resources("AWS::CertificateManager::Certificate")
    (cert,) = certs.values()
    options = cert["Properties"].get("DomainValidationOptions", [])
    # CDK emits a HostedZoneId reference inside the validation options
    has_hosted_zone_ref = any("HostedZoneId" in str(opt) for opt in options)
    assert has_hosted_zone_ref, (
        "Cert validation options must reference the hosted zone ID"
    )
