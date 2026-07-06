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
    )
