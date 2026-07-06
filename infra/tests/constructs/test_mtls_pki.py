# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.temporal.mtls_pki import (
    TemporalMtlsPki,
    TemporalMtlsPkiProps,
)


def _synth(*, env_name: str = "staging") -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    TemporalMtlsPki(
        stack,
        "MtlsPki",
        props=TemporalMtlsPkiProps(env_name=env_name),
    )
    return assertions.Template.from_stack(stack)


def test_two_certificate_authorities_are_created() -> None:
    """One root + one subordinate."""
    template = _synth()
    template.resource_count_is("AWS::ACMPCA::CertificateAuthority", 2)


def test_root_ca_is_type_root() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::ACMPCA::CertificateAuthority", {"Type": "ROOT"}
    )


def test_subordinate_ca_is_type_subordinate() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::ACMPCA::CertificateAuthority", {"Type": "SUBORDINATE"}
    )


def test_root_subject_uses_env_specific_common_name() -> None:
    template = _synth(env_name="staging")
    cas = template.find_resources(
        "AWS::ACMPCA::CertificateAuthority",
        {"Properties": {"Type": "ROOT"}},
    )
    (root,) = cas.values()
    assert (
        root["Properties"]["Subject"]["CommonName"] == "cert-ra-temporal-root-staging"
    )


def test_subordinate_subject_uses_env_specific_common_name() -> None:
    template = _synth(env_name="prod")
    cas = template.find_resources(
        "AWS::ACMPCA::CertificateAuthority",
        {"Properties": {"Type": "SUBORDINATE"}},
    )
    (sub,) = cas.values()
    assert sub["Properties"]["Subject"]["CommonName"] == "cert-ra-temporal-ca-prod"


def test_per_env_cas_means_staging_and_prod_have_different_subjects() -> None:
    """A5: per-env PCA prevents staging worker certs from being trusted by prod."""
    staging = _synth(env_name="staging")
    prod = _synth(env_name="prod")
    staging_cn = next(
        iter(staging.find_resources("AWS::ACMPCA::CertificateAuthority").values())
    )["Properties"]["Subject"]["CommonName"]
    prod_cn = next(
        iter(prod.find_resources("AWS::ACMPCA::CertificateAuthority").values())
    )["Properties"]["Subject"]["CommonName"]
    assert staging_cn != prod_cn


def test_two_acm_pca_certificates_issued() -> None:
    """Root self-sign + subordinate signed by root = 2 CfnCertificate resources."""
    template = _synth()
    template.resource_count_is("AWS::ACMPCA::Certificate", 2)


def test_two_activations_present() -> None:
    """One activation per CA."""
    template = _synth()
    template.resource_count_is("AWS::ACMPCA::CertificateAuthorityActivation", 2)


def test_both_activations_status_active_on_first_deploy() -> None:
    template = _synth()
    activations = template.find_resources("AWS::ACMPCA::CertificateAuthorityActivation")
    for activation in activations.values():
        assert activation["Properties"]["Status"] == "ACTIVE"


def test_subordinate_cert_uses_subordinate_template() -> None:
    template = _synth()
    certs = template.find_resources("AWS::ACMPCA::Certificate")
    templates = {c["Properties"]["TemplateArn"] for c in certs.values()}
    assert (
        "arn:aws:acm-pca:::template/SubordinateCACertificate_PathLen0/V1" in templates
    )


def test_root_cert_uses_root_template() -> None:
    template = _synth()
    certs = template.find_resources("AWS::ACMPCA::Certificate")
    templates = {c["Properties"]["TemplateArn"] for c in certs.values()}
    assert "arn:aws:acm-pca:::template/RootCACertificate/V1" in templates


def test_key_algorithm_is_ec_prime256v1() -> None:
    """ECC P-256 is sufficient for an internal PKI and rotates fast."""
    template = _synth()
    cas = template.find_resources("AWS::ACMPCA::CertificateAuthority")
    for ca in cas.values():
        assert ca["Properties"]["KeyAlgorithm"] == "EC_prime256v1"


def test_cas_retain_on_stack_delete() -> None:
    template = _synth()
    cas = template.find_resources("AWS::ACMPCA::CertificateAuthority")
    for ca in cas.values():
        assert ca.get("DeletionPolicy") == "Retain"
