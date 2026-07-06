# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from functools import cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TemporalSettings(BaseSettings):
    """Temporal.io client settings.

    Two connection modes are supported:

    - **Temporal Cloud** — set ``api_key`` (and ``host`` to the Cloud
      endpoint). The Python SDK enables TLS automatically when an
      API key is present.
    - **Self-hosted mTLS** — set ``tls_client_cert_content`` /
      ``tls_client_key_content`` / ``tls_ca_cert_content`` (PEM bytes
      injected by the WorkersStack / MaintenanceStack constructs from
      the per-service SeededSecret JSON fields). The connection helper
      builds a Temporal SDK ``TLSConfig`` from these bytes; no file
      mount is needed.

    The mTLS fields accept both the project-namespaced
    ``CERT_RA_TEMPORAL_TLS_*`` form and the bare ``TEMPORAL_TLS_*``
    form (which is what the infra layer emits, matching the Temporal
    SDK's own env-var convention).
    """

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_temporal_", case_sensitive=False, extra="ignore"
    )

    host: str = Field(
        default="localhost:7233",
        validation_alias=AliasChoices(
            "cert_ra_temporal_host",
            "temporal_address",
        ),
    )
    """Temporal server address. Accepts ``TEMPORAL_ADDRESS`` as an
    alias since that's what WorkersStack / MaintenanceStack emit."""

    namespace: str = "default"
    """Temporal namespace."""

    api_key: str = ""
    """Temporal Cloud API key. When set, TLS is enabled automatically.
    Mutually exclusive with the mTLS triplet below."""

    tls_client_cert_content: str = Field(
        default="",
        validation_alias=AliasChoices(
            "cert_ra_temporal_tls_client_cert_content",
            "temporal_tls_client_cert_content",
        ),
    )
    """PEM-encoded client certificate. Empty string means mTLS is off."""

    tls_client_key_content: str = Field(
        default="",
        validation_alias=AliasChoices(
            "cert_ra_temporal_tls_client_key_content",
            "temporal_tls_client_key_content",
        ),
    )
    """PEM-encoded client private key (matching the cert above)."""

    tls_ca_cert_content: str = Field(
        default="",
        validation_alias=AliasChoices(
            "cert_ra_temporal_tls_ca_cert_content",
            "temporal_tls_ca_cert_content",
        ),
    )
    """PEM-encoded CA chain for validating the Temporal frontend's cert."""

    tls_server_name: str = Field(
        default="",
        validation_alias=AliasChoices(
            "cert_ra_temporal_tls_server_name",
            "temporal_tls_server_name",
        ),
    )
    """SNI / cert-CN to validate against the Temporal frontend cert.
    Defaults to empty (SDK uses the hostname from ``host``); usually
    set to ``temporal-frontend.cert-ra.local`` for self-hosted."""

    alerts_enabled: bool = False
    """If False, the alerts worker exits cleanly on startup with a
    log message. Used to land the alerts worker dark before flipping
    it on in production. Read by ``cert_ra.alerts.worker.run_worker``."""

    worker_max_concurrent_activities: int = 100
    """Upper bound on activities a single metrics worker runs at once.

    Passed straight to ``Worker(max_concurrent_activities=...)``. The default
    matches the SDK's, but making it explicit lets ops raise/lower the ceiling
    without a code change. Note this cap is shared across *all* activity types
    on the ``metrics`` queue, so it gates the whole worker, not just markets."""

    market_fanout_concurrency: int = 6
    """How many market collect/score activities a single market workflow tick
    dispatches in parallel.

    The collector/scorer activities shell out to an LLM (``yarn --llm claude``),
    so this is deliberately bounded to respect Anthropic rate limits and cost
    rather than fanning out all markets at once. Each tick processes markets in
    batches of this size. Raise it (and ``worker_max_concurrent_activities`` +
    the DB ``pool_size``) to go faster once you have API headroom.

    Passed to the market workflows as their run argument when the schedule is
    created; changing it takes effect for schedules created after the change."""

    @property
    def tls_enabled(self) -> bool:
        """True when either an API key or the mTLS triplet is configured."""
        return bool(self.api_key) or self.mtls_enabled

    @property
    def mtls_enabled(self) -> bool:
        """True when all three PEM fields are non-empty.

        Self-hosted mTLS mode. Partial config is treated as off —
        half a triplet is always a misconfiguration, not "mTLS without
        CA verify".
        """
        return bool(
            self.tls_client_cert_content
            and self.tls_client_key_content
            and self.tls_ca_cert_content
        )


@cache
def get_temporal_settings() -> TemporalSettings:
    """Get cached TemporalSettings instance."""
    return TemporalSettings()
