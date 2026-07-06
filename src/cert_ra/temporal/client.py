# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Shared Temporal client connection helper.

Picks one of two connection modes based on ``TemporalSettings``:

- **Self-hosted mTLS** (preferred for production) — builds a
  ``temporalio.service.TLSConfig`` from the PEM bytes injected by
  WorkersStack / MaintenanceStack as ECS Secrets. No file mount; the
  Python SDK accepts in-memory PEM directly.
- **Temporal Cloud** — passes the API key + RPC metadata.

When neither is configured the connection is plain gRPC (development
/ local docker-compose).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import TLSConfig

if TYPE_CHECKING:
    from cert_ra.settings.temporal import TemporalSettings


async def connect_temporal(
    settings: TemporalSettings,
    **client_kwargs: Any,  # noqa: ANN401 — forwarded directly to Client.connect
) -> Client:
    """Construct a Temporal ``Client`` using the right auth mode.

    Args:
        settings: The cached ``TemporalSettings`` instance.
        **client_kwargs: Extra kwargs to forward to ``Client.connect``
            (e.g. ``identity``, ``interceptors``). ``data_converter``
            defaults to the pydantic converter.

    Returns:
        A connected ``Client``.

    Raises:
        ValueError: If both Temporal Cloud and self-hosted mTLS
            credentials are configured. The two modes are mutually
            exclusive — having both is always a misconfiguration.
    """
    if settings.api_key and settings.mtls_enabled:
        msg = (
            "Both Temporal Cloud (api_key) and self-hosted mTLS triplet are "
            "configured; pick one. Set CERT_RA_TEMPORAL_API_KEY='' to disable "
            "Cloud, or clear TEMPORAL_TLS_CLIENT_CERT_CONTENT to disable mTLS."
        )
        raise ValueError(msg)

    client_kwargs.setdefault("data_converter", pydantic_data_converter)
    client_kwargs.setdefault("namespace", settings.namespace)

    if settings.mtls_enabled:
        tls = TLSConfig(
            client_cert=settings.tls_client_cert_content.encode("utf-8"),
            client_private_key=settings.tls_client_key_content.encode("utf-8"),
            server_root_ca_cert=settings.tls_ca_cert_content.encode("utf-8"),
            # Empty string disables SNI override; SDK falls back to the
            # hostname from `settings.host`. We set it explicitly when
            # the infra layer provides a CN to validate against.
            domain=settings.tls_server_name or None,
        )
        return await Client.connect(settings.host, tls=tls, **client_kwargs)

    if settings.api_key:
        # Temporal Cloud uses the API key + namespace metadata; the SDK
        # enables TLS automatically when an API key is present.
        rpc_metadata = client_kwargs.pop("rpc_metadata", None) or {
            "temporal-namespace": settings.namespace
        }
        return await Client.connect(
            settings.host,
            api_key=settings.api_key,
            rpc_metadata=rpc_metadata,
            **client_kwargs,
        )

    # Plaintext gRPC — only used for local dev / docker-compose. In
    # production neither api_key nor mTLS being set is a real
    # misconfiguration, but failing loud here would break dev so we
    # let the connection proceed.
    return await Client.connect(settings.host, **client_kwargs)
