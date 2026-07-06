# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for per-chain validator stake fetchers (HTTP mocked)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from cert_ra.metrics.decentralization import validators
from cert_ra.settings.rpc import RPCSettings

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


_URL_PROPERTY_TO_FIELD = {
    "ethereum_urls": "ethereum_public_rpcs",
    "solana_urls": "solana_public_rpcs",
    "polygon_urls": "polygon_public_rpcs",
    "avalanche_p_urls": "avalanche_p_public_rpcs",
    "avalanche_c_urls": "avalanche_c_public_rpcs",
}


def _patch_rpc(mocker: MockerFixture, **overrides: object) -> None:
    """Replace ``get_rpc_settings`` in each validator module with a stub.

    Accepts the computed ``<chain>_urls`` property names for convenience and
    routes them to the underlying ``<chain>_public_rpcs`` field.
    """
    translated: dict[str, object] = {}
    for key, value in overrides.items():
        field = _URL_PROPERTY_TO_FIELD.get(key, key)
        translated[field] = value
    settings = RPCSettings(**translated)  # type: ignore[arg-type]
    mocker.patch.object(validators, "get_rpc_settings", return_value=settings)


def _mock_transport(
    handler: httpx.MockTransport,
    mocker: MockerFixture,
) -> None:
    """Force ``httpx.AsyncClient`` in validators module to use ``handler``."""
    original = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = handler
        return original(*args, **kwargs)  # type: ignore[arg-type]

    mocker.patch.object(validators.httpx, "AsyncClient", side_effect=factory)


# ---------------------------------------------------------------------------
# Ethereum
# ---------------------------------------------------------------------------


async def test_fetch_ethereum_stakes_no_urls_raises(mocker: MockerFixture) -> None:
    _patch_rpc(mocker, ethereum_urls=[])
    with pytest.raises(RuntimeError, match="no Ethereum RPC URLs"):
        await validators.fetch_ethereum_stakes()


async def test_fetch_ethereum_stakes_parses_active_validators(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, ethereum_urls=["https://eth.example.com"])

    payload = {
        "data": [
            {
                "index": "1",
                "status": "active_ongoing",
                "validator": {"effective_balance": "32000000000"},
            },
            {
                "index": "2",
                "status": "pending_initialized",  # filtered out
                "validator": {"effective_balance": "32000000000"},
            },
            {
                "index": "3",
                "status": "active_slashed",
                "validator": {"effective_balance": "16000000000"},
            },
        ]
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    _mock_transport(httpx.MockTransport(handler), mocker)

    stakes = await validators.fetch_ethereum_stakes()
    assert stakes == [("1", 32.0), ("3", 16.0)]


async def test_fetch_ethereum_stakes_falls_back_on_failure(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(
        mocker,
        ethereum_urls=["https://primary.example.com", "https://fallback.example.com"],
    )

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "primary" in str(request.url):
            return httpx.Response(500, text="boom")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "index": "1",
                        "status": "active_ongoing",
                        "validator": {"effective_balance": "32000000000"},
                    }
                ]
            },
        )

    _mock_transport(httpx.MockTransport(handler), mocker)

    stakes = await validators.fetch_ethereum_stakes()
    assert len(calls) == 2
    assert stakes == [("1", 32.0)]


async def test_fetch_ethereum_stakes_all_fail_raises(mocker: MockerFixture) -> None:
    _patch_rpc(mocker, ethereum_urls=["https://a.example", "https://b.example"])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="err")

    _mock_transport(httpx.MockTransport(handler), mocker)

    with pytest.raises(RuntimeError, match="all Ethereum beacon nodes failed"):
        await validators.fetch_ethereum_stakes()


# ---------------------------------------------------------------------------
# Solana
# ---------------------------------------------------------------------------


async def test_fetch_solana_stakes_no_urls_raises(mocker: MockerFixture) -> None:
    _patch_rpc(mocker, solana_urls=[])
    with pytest.raises(RuntimeError, match="no Solana RPC URLs"):
        await validators.fetch_solana_stakes()


async def test_fetch_solana_stakes_parses_vote_accounts(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, solana_urls=["https://sol.example"])

    payload = {
        "result": {
            "current": [
                {"votePubkey": "VOTE_A", "activatedStake": 1_000_000_000},
                {"votePubkey": "VOTE_B", "activatedStake": 2_500_000_000},
            ]
        }
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload).encode())

    _mock_transport(httpx.MockTransport(handler), mocker)

    stakes = await validators.fetch_solana_stakes()
    assert stakes == [("VOTE_A", 1.0), ("VOTE_B", 2.5)]


# ---------------------------------------------------------------------------
# Polygon
# ---------------------------------------------------------------------------


async def test_fetch_polygon_stakes_pages_and_stops(mocker: MockerFixture) -> None:
    pages = [
        {
            "success": True,
            "result": [
                {"id": 1, "totalStaked": 1_000_000_000_000_000_000_000},  # 1_000 POL
                {"id": 2, "totalStaked": 2_000_000_000_000_000_000_000},  # 2_000 POL
            ],
            "summary": {"total": 3, "size": 2},
        },
        {
            "success": True,
            "result": [
                {"id": 3, "totalStaked": 500_000_000_000_000_000_000}
            ],  # 500 POL
            "summary": {"total": 3, "size": 1},
        },
    ]
    responses = iter(pages)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    _mock_transport(httpx.MockTransport(handler), mocker)

    stakes = await validators.fetch_polygon_stakes()
    assert stakes == [("1", 1000.0), ("2", 2000.0), ("3", 500.0)]


async def test_fetch_polygon_stakes_success_false_raises(
    mocker: MockerFixture,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"success": False, "result": [], "summary": {"total": 0, "size": 0}},
        )

    _mock_transport(httpx.MockTransport(handler), mocker)

    with pytest.raises(RuntimeError, match="success=false"):
        await validators.fetch_polygon_stakes()


# ---------------------------------------------------------------------------
# Avalanche
# ---------------------------------------------------------------------------


async def test_fetch_avalanche_stakes_no_urls_raises(mocker: MockerFixture) -> None:
    _patch_rpc(mocker, avalanche_p_public_rpcs=[])
    with pytest.raises(RuntimeError, match="no Avalanche RPC URLs"):
        await validators.fetch_avalanche_stakes()


async def test_fetch_avalanche_stakes_sums_stake_and_delegation(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, avalanche_p_public_rpcs=["https://avax.example"])

    payload = {
        "result": {
            "validators": [
                {
                    "nodeID": "NodeID-A",
                    "stakeAmount": 2_000_000_000,  # 2 AVAX
                    "delegatorWeight": 1_000_000_000,  # 1 AVAX
                },
                {
                    "nodeID": "NodeID-B",
                    "stakeAmount": None,
                    "delegatorWeight": 5_000_000_000,  # 5 AVAX
                },
            ]
        }
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload).encode())

    _mock_transport(httpx.MockTransport(handler), mocker)

    stakes = await validators.fetch_avalanche_stakes()
    assert stakes == [("NodeID-A", 3.0), ("NodeID-B", 5.0)]
