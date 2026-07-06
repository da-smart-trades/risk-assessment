# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_acmpca as acmpca
from cdk_nag import NagSuppressions
from constructs import Construct

ROOT_CA_TEMPLATE_ARN = "arn:aws:acm-pca:::template/RootCACertificate/V1"
SUBORDINATE_CA_TEMPLATE_ARN = (
    "arn:aws:acm-pca:::template/SubordinateCACertificate_PathLen0/V1"
)
DEFAULT_KEY_ALGORITHM = "EC_prime256v1"
DEFAULT_SIGNING_ALGORITHM = "SHA256WITHECDSA"


@dataclass(frozen=True, slots=True)
class TemporalMtlsPkiProps:
    """Props for TemporalMtlsPki. See § Temporal mTLS (M5) and § PCA structure
    (B2 resolved) in the design spec.

    Per A5, each environment gets its **own** root + subordinate CA pair
    (no sharing between staging and prod) so a compromise of a staging
    worker cert cannot authenticate against prod's Temporal frontend.
    """

    env_name: str
    """e.g. `staging` or `prod`. Used in CA names and CommonNames."""

    organization: str = "Certora"
    """O= in the CA subject DN."""

    country: str = "US"
    """C= in the CA subject DN."""

    root_validity_years: int = 10
    """Root CA cert validity. Standard for offline-ish root."""

    subordinate_validity_years: int = 5
    """Subordinate CA cert validity. Half of root by convention; rotated
    every 5 years via a documented runbook (`docs/temporal-mtls-rotation.md`)."""


class TemporalMtlsPki(Construct):
    """Per-environment ACM Private CA hierarchy backing the Temporal mTLS
    fabric.

    Two CAs:
    - **Root** (self-signed): signs the subordinate; intended to be
      DISABLED after subordinate issuance so it can't issue further
      end-entity certs without an explicit re-enable + MFA step (B2
      tradeoff — we accept the residual risk that an attacker with
      Installer + MFA can re-enable the root and mint new sub-CAs;
      CloudTrail visible).
    - **Subordinate**: signed by the root. Day-to-day issuer of the five
      end-entity certs (`temporal-frontend`, three workers, `maint`).

    Scope of THIS PR:
    - Create both CAs
    - Self-sign the root and activate it (status=ACTIVE)
    - Issue the subordinate's cert from the root and activate it
    - Both CAs remain ACTIVE on initial deploy; **disabling the root is
      a follow-up PR** with a small Custom Resource that flips
      `status=DISABLED` after subordinate issuance is confirmed.

    Deferred to follow-up PRs:
    - End-entity cert issuance (InitialCertIssuance Custom Resource per
      B1 path 1): generates a private key + CSR locally, calls
      `acm-pca:IssueCertificate` against the subordinate, writes
      `(cert, chain, key)` to the per-service SeededSecret shells from
      SecretsStack.
    - Renewal handler (B1 path 2): EventBridge rule on `acm-pca`
      certificate-renewed events → Lambda that updates the same
      Secrets Manager entries.
    - Disable-root step (B2 finalization): Custom Resource that calls
      `acm-pca:UpdateCertificateAuthority` with status=DISABLED on the
      root after sub activation.
    """

    root_ca: acmpca.CfnCertificateAuthority
    root_certificate: acmpca.CfnCertificate
    root_activation: acmpca.CfnCertificateAuthorityActivation
    subordinate_ca: acmpca.CfnCertificateAuthority
    subordinate_certificate: acmpca.CfnCertificate
    subordinate_activation: acmpca.CfnCertificateAuthorityActivation

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: TemporalMtlsPkiProps,
    ) -> None:
        super().__init__(scope, construct_id)

        env = props.env_name
        org = props.organization
        country = props.country

        # ---- Root CA ----
        self.root_ca = acmpca.CfnCertificateAuthority(
            self,
            "RootCa",
            type="ROOT",
            key_algorithm=DEFAULT_KEY_ALGORITHM,
            signing_algorithm=DEFAULT_SIGNING_ALGORITHM,
            subject=acmpca.CfnCertificateAuthority.SubjectProperty(
                common_name=f"cert-ra-temporal-root-{env}",
                organization=org,
                country=country,
            ),
        )
        self.root_ca.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        # Self-signed root cert. CDK feeds the CA's CSR back to itself.
        self.root_certificate = acmpca.CfnCertificate(
            self,
            "RootCertificate",
            certificate_authority_arn=self.root_ca.attr_arn,
            certificate_signing_request=self.root_ca.attr_certificate_signing_request,
            signing_algorithm=DEFAULT_SIGNING_ALGORITHM,
            template_arn=ROOT_CA_TEMPLATE_ARN,
            validity=acmpca.CfnCertificate.ValidityProperty(
                type="YEARS", value=props.root_validity_years
            ),
        )

        self.root_activation = acmpca.CfnCertificateAuthorityActivation(
            self,
            "RootActivation",
            certificate_authority_arn=self.root_ca.attr_arn,
            certificate=self.root_certificate.attr_certificate,
            status="ACTIVE",
        )

        # ---- Subordinate CA ----
        self.subordinate_ca = acmpca.CfnCertificateAuthority(
            self,
            "SubordinateCa",
            type="SUBORDINATE",
            key_algorithm=DEFAULT_KEY_ALGORITHM,
            signing_algorithm=DEFAULT_SIGNING_ALGORITHM,
            subject=acmpca.CfnCertificateAuthority.SubjectProperty(
                common_name=f"cert-ra-temporal-ca-{env}",
                organization=org,
                country=country,
            ),
        )
        self.subordinate_ca.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        # Subordinate's cert is signed by the root.
        self.subordinate_certificate = acmpca.CfnCertificate(
            self,
            "SubordinateCertificate",
            certificate_authority_arn=self.root_ca.attr_arn,
            certificate_signing_request=self.subordinate_ca.attr_certificate_signing_request,
            signing_algorithm=DEFAULT_SIGNING_ALGORITHM,
            template_arn=SUBORDINATE_CA_TEMPLATE_ARN,
            validity=acmpca.CfnCertificate.ValidityProperty(
                type="YEARS", value=props.subordinate_validity_years
            ),
        )
        # The subordinate CSR isn't available until the root is activated,
        # so its cert depends on root activation succeeding.
        self.subordinate_certificate.add_dependency(self.root_activation)

        self.subordinate_activation = acmpca.CfnCertificateAuthorityActivation(
            self,
            "SubordinateActivation",
            certificate_authority_arn=self.subordinate_ca.attr_arn,
            certificate=self.subordinate_certificate.attr_certificate,
            certificate_chain=self.root_certificate.attr_certificate,
            status="ACTIVE",
        )

        # The root + subordinate cert authority resources auto-create
        # CloudWatch log groups + IAM roles for ACM PCA audit logging
        # internals; we don't author those policies. Suppress cdk-nag
        # findings on them.
        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "ACM PCA service-managed roles use wildcard resources on "
                        "acm-pca:* internal actions; we don't control the policy "
                        "shape (CFN-managed)."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                    "reason": (
                        "ACM PCA service-managed roles are created with inline "
                        "policies by ACM PCA itself; we don't author them."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @property
    def root_ca_arn(self) -> str:
        return self.root_ca.attr_arn

    @property
    def subordinate_ca_arn(self) -> str:
        return self.subordinate_ca.attr_arn

    @property
    def root_certificate_pem(self) -> str:
        """The root cert PEM (token), suitable for the trust bundle."""
        return self.root_certificate.attr_certificate
