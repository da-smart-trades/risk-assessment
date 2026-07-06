# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from functools import cache

from pydantic import SecretStr  # noqa: TC002 — runtime-resolved by pydantic
from pydantic_settings import BaseSettings, SettingsConfigDict


class DuneSettings(BaseSettings):
    """Dune Analytics API settings.

    Environment variable examples::

        CERT_RA_DUNE_API_KEY=...
        CERT_RA_DUNE_BASE_URL=https://api.dune.com/api/v1
    """

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_dune_", case_sensitive=False, extra="ignore"
    )

    api_key: SecretStr | None = None
    """Dune Analytics API key."""
    base_url: str = "https://api.dune.com/api/v1"
    """Base URL of the Dune REST API."""
    performance: str = "medium"
    """Query performance tier (``medium`` or ``large``)."""
    poll_interval_seconds: float = 2.0
    """Seconds between status polls while waiting for a query to finish."""
    poll_timeout_seconds: float = 180.0
    """Maximum time to wait for a single query to finish."""


@cache
def get_dune_settings() -> DuneSettings:
    """Get a cached ``DuneSettings`` instance."""
    return DuneSettings()
