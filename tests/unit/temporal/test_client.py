# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unit tests for cert_ra.temporal.client.connect_temporal."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cert_ra.settings.temporal import TemporalSettings
from cert_ra.temporal.client import connect_temporal


def _settings(**overrides: Any) -> TemporalSettings:
    """Build a TemporalSettings without reading the process env.

    Using direct field assignment via the constructor skips the
    BaseSettings env loader so each test stays hermetic.
    """
    base: dict[str, Any] = {
        "host": "localhost:7233",
        "namespace": "default",
        "api_key": "",
        "tls_client_cert_content": "",
        "tls_client_key_content": "",
        "tls_ca_cert_content": "",
        "tls_server_name": "",
        "alerts_enabled": False,
    }
    base.update(overrides)
    return TemporalSettings.model_construct(**base)


@pytest.mark.anyio
async def test_connects_with_mtls_when_triplet_is_set() -> None:
    """Self-hosted mTLS path: build a TLSConfig from the three PEM
    contents and pass it to Client.connect.
    """
    settings = _settings(
        host="temporal.internal:7233",
        tls_client_cert_content="-----BEGIN CERTIFICATE-----\nFAKECERT\n-----END CERTIFICATE-----",
        tls_client_key_content="-----BEGIN PRIVATE KEY-----\nFAKEKEY\n-----END PRIVATE KEY-----",
        tls_ca_cert_content="-----BEGIN CERTIFICATE-----\nFAKECA\n-----END CERTIFICATE-----",
        tls_server_name="temporal-frontend.cert-ra.local",
    )

    with patch(
        "cert_ra.temporal.client.Client.connect", new=AsyncMock()
    ) as mock_connect:
        await connect_temporal(settings)

    mock_connect.assert_awaited_once()
    args, kwargs = mock_connect.await_args
    assert args[0] == "temporal.internal:7233"
    tls = kwargs["tls"]
    # TLSConfig is a NamedTuple from temporalio.service; assert the
    # PEM bytes round-trip through the encoding.
    assert tls.client_cert == settings.tls_client_cert_content.encode("utf-8")
    assert tls.client_private_key == settings.tls_client_key_content.encode("utf-8")
    assert tls.server_root_ca_cert == settings.tls_ca_cert_content.encode("utf-8")
    assert tls.domain == "temporal-frontend.cert-ra.local"
    # api_key must NOT have been threaded through in mTLS mode.
    assert "api_key" not in kwargs
    assert kwargs["namespace"] == "default"


@pytest.mark.anyio
async def test_connects_with_api_key_when_set() -> None:
    """Temporal Cloud path: pass api_key + rpc_metadata; do NOT build TLSConfig."""
    settings = _settings(
        host="cert-ra.tmprl.cloud:7233",
        namespace="cert-ra.acme",
        api_key="apikey-12345",
    )

    with patch(
        "cert_ra.temporal.client.Client.connect", new=AsyncMock()
    ) as mock_connect:
        await connect_temporal(settings)

    args, kwargs = mock_connect.await_args
    assert args[0] == "cert-ra.tmprl.cloud:7233"
    assert kwargs["api_key"] == "apikey-12345"
    assert kwargs["rpc_metadata"] == {"temporal-namespace": "cert-ra.acme"}
    assert "tls" not in kwargs


@pytest.mark.anyio
async def test_connects_plaintext_when_neither_mode_is_configured() -> None:
    """Dev/docker-compose: no api_key, no mTLS triplet — plain gRPC."""
    settings = _settings()

    with patch(
        "cert_ra.temporal.client.Client.connect", new=AsyncMock()
    ) as mock_connect:
        await connect_temporal(settings)

    args, kwargs = mock_connect.await_args
    assert args[0] == "localhost:7233"
    assert "tls" not in kwargs
    assert "api_key" not in kwargs


@pytest.mark.anyio
async def test_refuses_to_connect_when_both_modes_are_configured() -> None:
    """Misconfiguration: api_key + mTLS triplet both set. The two modes
    are mutually exclusive — fail loud rather than silently picking one.
    """
    settings = _settings(
        api_key="apikey-12345",
        tls_client_cert_content="-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----",
        tls_client_key_content="-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----",
        tls_ca_cert_content="-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----",
    )

    with pytest.raises(ValueError, match="Both Temporal Cloud .* and self-hosted mTLS"):
        await connect_temporal(settings)


@pytest.mark.anyio
async def test_partial_mtls_triplet_is_treated_as_disabled() -> None:
    """Half a triplet is always a misconfiguration, not 'mTLS without
    CA verify'. The mtls_enabled property is False so the connection
    falls back to plaintext (or api_key).
    """
    settings = _settings(
        tls_client_cert_content="-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----",
        # tls_client_key_content and tls_ca_cert_content left empty.
    )
    assert settings.mtls_enabled is False
    assert settings.tls_enabled is False


@pytest.mark.anyio
async def test_mtls_server_name_empty_string_becomes_none_for_sni_override() -> None:
    """When tls_server_name is "" the SDK shouldn't override SNI — we
    pass None so it falls back to the hostname from settings.host.
    """
    settings = _settings(
        tls_client_cert_content="-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----",
        tls_client_key_content="-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----",
        tls_ca_cert_content="-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----",
        tls_server_name="",
    )

    with patch(
        "cert_ra.temporal.client.Client.connect", new=AsyncMock()
    ) as mock_connect:
        await connect_temporal(settings)

    _, kwargs = mock_connect.await_args
    assert kwargs["tls"].domain is None
