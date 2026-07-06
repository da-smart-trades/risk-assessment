# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class MarketMetricsSettings(BaseSettings):
    """Configuration for the automated market metrics workers.

    The collector and scorer Temporal workflows shell out to a ``yarn``
    project at ``yarn_cwd``. The CLI shape is::

        yarn <protocol> [--score] --llm claude --output json <chain_id> <market_id_hex>

    Environment variable examples::

        CERT_RA_MARKET_METRICS_YARN_CWD=/opt/risk-yarn
        CERT_RA_MARKET_METRICS_YARN_TIMEOUT_SECONDS=110.0

    ``yarn_cwd`` is required at the call site (the activity raises if
    unset) — the worker can still boot without it so unrelated workflows
    in the same worker process are unaffected.
    """

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_market_metrics_",
        case_sensitive=False,
        extra="ignore",
    )

    yarn_cwd: str | None = None
    """Working directory of the yarn project that produces market metrics."""
    yarn_timeout_seconds: float = 110.0
    """Soft per-invocation timeout. Leaves ~10s headroom inside the
    Temporal activity's ``start_to_close_timeout`` of 2 minutes."""


@cache
def get_market_metrics_settings() -> MarketMetricsSettings:
    """Get a cached ``MarketMetricsSettings`` instance."""
    return MarketMetricsSettings()
