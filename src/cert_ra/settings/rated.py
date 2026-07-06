# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from functools import cache

from pydantic import SecretStr  # noqa: TC002 — runtime-resolved by pydantic
from pydantic_settings import BaseSettings, SettingsConfigDict


class RatedSettings(BaseSettings):
    """Rated Network API settings.

    The Rated Network API exposes per-operator aggregated validator data for
    Ethereum (pools, custodians, solo stakers). We use it to map raw Beacon
    validators to their operating entity so the Nakamoto coefficient can be
    reported at the operator level rather than the validator-slot level.

    Environment variable examples::

        CERT_RA_RATED_API_KEY=...
        CERT_RA_RATED_BASE_URL=https://api.rated.network/v0
    """

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_rated_", case_sensitive=False, extra="ignore"
    )

    api_key: SecretStr | None = None
    """Rated Network API key (Bearer token). Required for operator refresh."""
    base_url: str = "https://api.rated.network/v0"
    """Base URL of the Rated Network REST API."""
    window: str = "1d"
    """Aggregation window passed to ``/eth/operators`` (e.g. ``1d``, ``7d``)."""
    page_size: int = 100
    """Page size for paginated operator queries."""


@cache
def get_rated_settings() -> RatedSettings:
    """Get a cached ``RatedSettings`` instance."""
    return RatedSettings()
