# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cert_ra_infra.config import load_config

EnvName = Literal["staging", "prod"]


@dataclass(frozen=True, slots=True)
class EnvConfig:
    env: EnvName
    region: str
    domain: str

    # Networking sizing — different CIDR per env so VPCs can be peered
    # later without renumbering.
    vpc_cidr: str
    max_azs: int
    nat_gateways: int

    # ID of a pre-existing public Route53 hosted zone for `domain`. When
    # set, DnsStack REFERENCES that zone instead of creating one, so the
    # zone's lifecycle (and its nameservers) are decoupled from the cert:
    # a cert-validation rollback can never destroy the zone or churn the
    # NS, which would otherwise force a fresh Cloudflare delegation every
    # retry. When None, DnsStack creates the zone (with RETAIN) and emits
    # the NameServers output for the one-time Cloudflare delegation; once
    # that zone exists, capture its ID here to make it permanent.
    dns_zone_id: str | None = None

    # --- Domain migration (active while `next_domain` is set) ---------
    # Moving an env to a new public hostname without touching the old
    # domain's parent DNS (which we may not control). While `next_domain`
    # is set, DnsStack keeps the old zone/cert AND builds a second
    # HostedZoneWithCert for `next_domain`; AppStack serves on the new
    # domain and drops the old alias records (the old name goes dark).
    # The old cert's cross-stack export is pinned so CloudFormation
    # accepts the two-step cutover (deploy DnsStack, then AppStack).
    next_domain: str | None = None

    # Route53 zone id/name of `next_domain`'s PARENT zone (e.g. the zone
    # for `example.com` when next_domain is `risk.example.com`). When the
    # parent zone lives in the same AWS account, setting these makes
    # DnsStack write the NS delegation record itself — no out-of-band
    # registrar step. Leave unset if the parent is hosted elsewhere and
    # delegate manually (see the NextNameServers stack output).
    next_dns_parent_zone_id: str | None = None
    next_dns_parent_zone_name: str | None = None

    # Like `dns_zone_id`, but for `next_domain`: None on the first deploy
    # (DnsStack creates the zone with RETAIN); capture the created zone's
    # ID here afterwards so later deploys reference it.
    next_dns_zone_id: str | None = None

    @property
    def active_domain(self) -> str:
        """The public hostname the app should serve on right now."""
        return self.next_domain or self.domain


# Per-environment settings come from the bootstrap deployment config
# (deployment.config.json, falling back to deployment.config.example.json) so a
# new owner supplies their own region/domain/zone without editing this source.
# The AWS account ID is read from CDK at synth time via Stack.of(self).account —
# see § Construct conventions / "Account ID at synth time" in the design spec.
def load_env(name: str) -> EnvConfig:
    environments = load_config()["environments"]
    if name not in environments:
        valid = ", ".join(sorted(environments))
        raise ValueError(f"unknown CDK_ENV={name!r}; valid: {valid}")
    raw = environments[name]
    return EnvConfig(
        env=name,  # type: ignore[arg-type]
        region=raw["region"],
        domain=raw["domain"],
        vpc_cidr=raw["vpc_cidr"],
        max_azs=raw["max_azs"],
        nat_gateways=raw["nat_gateways"],
        dns_zone_id=raw.get("dns_zone_id"),
        next_domain=raw.get("next_domain"),
        next_dns_parent_zone_id=raw.get("next_dns_parent_zone_id"),
        next_dns_parent_zone_name=raw.get("next_dns_parent_zone_name"),
        next_dns_zone_id=raw.get("next_dns_zone_id"),
    )
