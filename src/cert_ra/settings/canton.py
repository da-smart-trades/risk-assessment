# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from functools import cache

from pydantic import SecretStr  # noqa: TC002 — runtime-resolved by pydantic
from pydantic_settings import BaseSettings, SettingsConfigDict


class CantonSettings(BaseSettings):
    """Canton Network (Global Synchronizer) Scan API settings.

    Canton has no blocks/slots and no PoS validator set; the public data
    surface is the Splice **Scan API**, hosted redundantly by each Super
    Validator (SV). MainNet Scan URLs are discoverable at
    ``https://sync.global/sv-network/``. We treat the configured URLs as Scan
    API roots (e.g. ``https://scan.<sv>.global.canton.network.../api/scan``)
    and append versioned paths such as ``/v0/dso`` and ``/v2/updates``.

    Multiple URLs act as ordered fallbacks: each call tries them in turn until
    one responds, mirroring the per-chain RPC fallback in
    :class:`cert_ra.settings.rpc.RPCSettings`. Pointing at several independent
    SV Scans also matches Canton's own trust model — no single SV need be
    trusted.

    Some MainNet Scans gate reads behind an auth token; set ``api_token`` to
    have it sent as a ``Bearer`` header.

    Environment variable examples::

        CERT_RA_CANTON_SCAN_URLS='["https://scan.sv-1.example/api/scan"]'
        CERT_RA_CANTON_API_TOKEN=...
    """

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_canton_", case_sensitive=False, extra="ignore"
    )

    scan_urls: list[str] = []
    """Scan API root URLs (tried in order). At least one is required for the
    Canton metric workflows to produce data."""

    api_token: SecretStr | None = None
    """Optional bearer token for Scan deployments that gate reads."""

    request_timeout_seconds: float = 30.0
    """Per-request HTTP timeout for Scan calls."""

    migration_id: int = 4
    """Current Global Synchronizer migration id, used to scope the ACS
    snapshot-timestamp lookup (ledger-freshness signal). Bumps only on a
    synchronizer migration; if it drifts the freshness call fails soft and the
    snapshot still persists with a ``-1`` sentinel."""

    updates_window_seconds: int = 60
    """Look-back window used to estimate updates-per-second from the bulk
    ``/v2/updates`` stream."""

    updates_page_size: int = 1000
    """Page size requested from ``/v2/updates`` (the Scan API caps this at
    1000). When the window's update count reaches this cap the resulting rate
    is a floor (logged by the fetcher)."""

    validator_license_page_size: int = 1000
    """Page size for paginating ``/v0/admin/validator/licenses``."""

    validator_license_max_pages: int = 50
    """Safety cap on validator-license pages walked per snapshot."""


@cache
def get_canton_settings() -> CantonSettings:
    """Get a cached ``CantonSettings`` instance."""
    return CantonSettings()
