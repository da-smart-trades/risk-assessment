# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass, field

from aws_cdk import RemovalPolicy
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_route53 as route53
from constructs import Construct


def _empty_str_list() -> list[str]:
    return []


@dataclass(frozen=True, slots=True)
class HostedZoneWithCertProps:
    """Props for HostedZoneWithCert. See § Resource ownership matrix —
    DnsStack owns the ACM cert; the hosted zone is either created here or
    referenced (see `hosted_zone_id`)."""

    domain_name: str
    """The fully-qualified domain (e.g. `cert-ra.staging.certora.com`)."""

    hosted_zone_id: str | None = None
    """If set, REFERENCE this existing public hosted zone instead of
    creating one. Decouples the zone's lifecycle from the cert so a
    cert-validation rollback can't destroy the zone or change its NS
    (which would force a new Cloudflare delegation). If None, the zone is
    created (with RETAIN) and its NS exposed for a one-time delegation."""

    subject_alternative_names: list[str] = field(default_factory=_empty_str_list)
    """Optional SANs on the cert (e.g. `*.cert-ra.staging.certora.com`)."""

    add_wildcard_san: bool = True
    """If True, automatically add `*.{domain_name}` as a SAN. Default lets
    sub-paths under the domain (e.g. `api.cert-ra.staging.certora.com`)
    use the same cert."""


class HostedZoneWithCert(Construct):
    """Public Route53 hosted zone + ACM cert validated by DNS records in
    that zone.

    The hosted zone has authoritative NS records that must be delegated
    from the parent zone (`certora.com`). The parent zone lives at
    **Cloudflare** (the registrar for `certora.com`), so the four NS
    records emitted by this construct must be added at Cloudflare's
    DNS console — not at AWS — for delegation to complete.

    With wildcard SAN enabled (default), one ACM cert covers the apex
    and every subdomain (`risk.example.com` + `www.risk.example.com` +
    `api.risk.example.com` ...). DNS validation uses the Route53 zone
    we own under the subdomain — Cloudflare only needs the one NS
    delegation; no per-cert CNAME copying.
    """

    hosted_zone: route53.IHostedZone
    certificate: acm.Certificate
    owns_zone: bool

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: HostedZoneWithCertProps,
    ) -> None:
        super().__init__(scope, construct_id)

        if props.hosted_zone_id is not None:
            # Reference an existing, already-delegated public zone. The cert
            # below validates against it via DNS, but the zone itself is not
            # a resource of this stack — so a cert-validation rollback can
            # never delete it or change its nameservers.
            self.owns_zone = False
            self.hosted_zone = route53.PublicHostedZone.from_hosted_zone_attributes(
                self,
                "Zone",
                hosted_zone_id=props.hosted_zone_id,
                zone_name=props.domain_name,
            )
        else:
            # Create the zone. RETAIN so a failed/rolled-back deploy leaves
            # it (and its nameservers) intact — capture its ID into
            # EnvConfig.dns_zone_id afterwards to switch to the reference
            # path above and end the per-retry re-delegation churn.
            self.owns_zone = True
            self.hosted_zone = route53.PublicHostedZone(
                self,
                "Zone",
                zone_name=props.domain_name,
                comment=f"cert-ra: public zone for {props.domain_name}",
            )
            self.hosted_zone.apply_removal_policy(RemovalPolicy.RETAIN)

        sans = list(props.subject_alternative_names)
        if props.add_wildcard_san:
            wildcard = f"*.{props.domain_name}"
            if wildcard not in sans:
                sans.append(wildcard)

        # The parent zone `certora.com` publishes a restrictive CAA record
        # set (letsencrypt/digicert/sectigo/google/ssl.com) that does NOT
        # authorize Amazon. CAA is inherited by subdomains, so ACM is
        # forbidden from issuing for this domain and the cert goes to FAILED
        # within ~1-2 min of the validation check. Publish a CAA record at
        # THIS subdomain authorizing Amazon (issue + issuewild) — a more
        # specific CAA overrides the parent policy for this name only. The
        # cert must not be requested until that record exists, so depend on
        # it explicitly.
        caa = route53.CaaRecord(
            self,
            "CaaAmazon",
            zone=self.hosted_zone,
            values=[
                route53.CaaRecordValue(
                    flag=0, tag=route53.CaaTag.ISSUE, value="amazon.com"
                ),
                route53.CaaRecordValue(
                    flag=0, tag=route53.CaaTag.ISSUEWILD, value="amazon.com"
                ),
            ],
        )

        self.certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=props.domain_name,
            subject_alternative_names=sans if sans else None,
            validation=acm.CertificateValidation.from_dns(self.hosted_zone),
        )
        self.certificate.node.add_dependency(caa)

    @property
    def hosted_zone_id(self) -> str:
        return self.hosted_zone.hosted_zone_id

    @property
    def hosted_zone_name(self) -> str:
        return self.hosted_zone.zone_name

    @property
    def certificate_arn(self) -> str:
        return self.certificate.certificate_arn

    @property
    def name_servers(self) -> list[str] | None:
        """Authoritative NS records for the zone, to be delegated in the
        parent zone before TLS validation succeeds (operator runbook step).

        Only available when this construct created the zone; a referenced
        zone is already delegated, so this returns None."""
        if not self.owns_zone:
            return None
        return self.hosted_zone.hosted_zone_name_servers
