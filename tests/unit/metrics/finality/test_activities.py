# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for finality fetch activities (HTTP mocked)."""

# ruff: noqa: EM102, TRY003 — f-string AssertionError is fine in mock handlers.

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from cert_ra.metrics.finality import activities
from cert_ra.settings.rpc import RPCSettings

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


_URL_PROPERTY_TO_FIELD = {
    "ethereum_urls": "ethereum_public_rpcs",
    "arbitrum_urls": "arbitrum_public_rpcs",
    "base_urls": "base_public_rpcs",
    "polygon_urls": "polygon_public_rpcs",
    "optimism_urls": "optimism_public_rpcs",
    "solana_urls": "solana_public_rpcs",
    "avalanche_p_urls": "avalanche_p_public_rpcs",
    "avalanche_c_urls": "avalanche_c_public_rpcs",
}


def _patch_rpc(mocker: MockerFixture, **overrides: object) -> None:
    translated: dict[str, object] = {}
    for key, value in overrides.items():
        field = _URL_PROPERTY_TO_FIELD.get(key, key)
        translated[field] = value
    settings = RPCSettings(**translated)  # type: ignore[arg-type]
    mocker.patch.object(activities, "get_rpc_settings", return_value=settings)


def _install_transport(mocker: MockerFixture, handler: httpx.MockTransport) -> None:
    original = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = handler
        return original(*args, **kwargs)  # type: ignore[arg-type]

    mocker.patch.object(activities.httpx, "AsyncClient", side_effect=factory)


def _eth_block_json(number: int, timestamp: int) -> dict[str, object]:
    return {"result": {"number": hex(number), "timestamp": hex(timestamp)}}


# ---------------------------------------------------------------------------
# Ethereum
# ---------------------------------------------------------------------------


async def test_fetch_ethereum_finality_happy_path(mocker: MockerFixture) -> None:
    _patch_rpc(mocker, ethereum_urls=["https://eth.example"])

    def handler(request: httpx.Request) -> httpx.Response:
        # Beacon GET call.
        if request.method == "GET" and "finality_checkpoints" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "current_justified": {"epoch": 100},
                        "finalized": {"epoch": 99},
                    }
                },
            )
        # JSON-RPC POST — inspect the request id to decide the reply.
        body = request.read()
        if b'"id":1' in body:
            return httpx.Response(200, json=_eth_block_json(1_000, 1_000_000_100))
        if b'"id":2' in body:
            return httpx.Response(200, json=_eth_block_json(900, 1_000_000_000))
        if b'"id":3' in body:
            return httpx.Response(200, json=_eth_block_json(950, 1_000_000_050))
        raise AssertionError(f"Unexpected request: {body!r}")

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_ethereum_finality()
    assert result.head_height == 1000
    assert result.finalized_height == 900
    assert result.safe_height == 950
    assert result.justified_epoch == 100
    assert result.finalized_epoch == 99
    assert result.justified_finalized_gap == 1
    assert result.head_to_finalized_time == 100


async def test_fetch_ethereum_finality_uses_sentinel_when_beacon_fails(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, ethereum_urls=["https://eth.example"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(502, text="bad gateway")
        body = request.read()
        if b'"id":1' in body:
            return httpx.Response(200, json=_eth_block_json(10, 100))
        if b'"id":2' in body:
            return httpx.Response(200, json=_eth_block_json(5, 50))
        if b'"id":3' in body:
            return httpx.Response(200, json=_eth_block_json(8, 80))
        raise AssertionError(f"Unexpected request: {body!r}")

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_ethereum_finality()
    # Beacon unavailable → sentinel epoch values.
    assert result.justified_epoch == -1
    assert result.finalized_epoch == -1
    assert result.justified_finalized_gap == -1


async def test_fetch_ethereum_finality_raises_when_all_nodes_fail(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, ethereum_urls=["https://eth.example"])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _install_transport(mocker, httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="all execution-layer RPC nodes failed"):
        await activities.fetch_ethereum_finality()


# ---------------------------------------------------------------------------
# EVM L2 (Arbitrum / Base)
# ---------------------------------------------------------------------------


async def test_fetch_evm_l2_finality_arbitrum_includes_hard_finality_fields(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, arbitrum_urls=["https://arb.example"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        if b'"id":1' in body:
            return httpx.Response(200, json=_eth_block_json(500, 1_000_000_200))
        if b'"id":2' in body:
            return httpx.Response(200, json=_eth_block_json(450, 1_000_000_150))
        if b'"id":3' in body:
            return httpx.Response(200, json=_eth_block_json(400, 1_000_000_100))
        raise AssertionError(body)

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_evm_l2_finality("ARBITRUM")
    assert result.chain == "ARBITRUM"
    assert result.latest_height == 500
    assert result.latest_to_safe_blocks == 50
    assert result.safe_to_finalized_blocks == 50
    assert result.height_correlation == 100
    assert result.time_to_hard_finality == 100


async def test_fetch_evm_l2_finality_base_omits_hard_finality_fields(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, base_urls=["https://base.example"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        if b'"id":1' in body:
            return httpx.Response(200, json=_eth_block_json(10, 100))
        if b'"id":2' in body:
            return httpx.Response(200, json=_eth_block_json(8, 80))
        if b'"id":3' in body:
            return httpx.Response(200, json=_eth_block_json(5, 50))
        raise AssertionError(body)

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_evm_l2_finality("BASE")
    assert result.chain == "BASE"
    assert result.height_correlation is None
    assert result.time_to_hard_finality is None


async def test_fetch_evm_l2_finality_optimism_includes_hard_finality_fields(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, optimism_urls=["https://op.example"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        if b'"id":1' in body:
            return httpx.Response(200, json=_eth_block_json(2_000, 1_000_000_200))
        if b'"id":2' in body:
            return httpx.Response(200, json=_eth_block_json(1_950, 1_000_000_150))
        if b'"id":3' in body:
            return httpx.Response(200, json=_eth_block_json(1_900, 1_000_000_100))
        raise AssertionError(body)

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_evm_l2_finality("OPTIMISM")
    assert result.chain == "OPTIMISM"
    assert result.latest_height == 2_000
    assert result.latest_to_safe_blocks == 50
    assert result.safe_to_finalized_blocks == 50
    assert result.height_correlation == 100
    assert result.time_to_hard_finality == 100


async def test_fetch_evm_l2_finality_raises_when_nodes_fail(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, arbitrum_urls=["https://arb.example"])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _install_transport(mocker, httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="all RPC nodes failed"):
        await activities.fetch_evm_l2_finality("ARBITRUM")


# ---------------------------------------------------------------------------
# OP Stack (Ink / Unichain)
# ---------------------------------------------------------------------------


async def test_fetch_op_stack_finality_parses_sync_status(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, ink_url="https://ink.example")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "unsafe_l2": {
                        "number": hex(1_000),
                        "timestamp": hex(1_000_000_100),
                    },
                    "safe_l2": {"number": hex(900), "timestamp": hex(1_000_000_050)},
                    "finalized_l2": {
                        "number": hex(800),
                        "timestamp": hex(1_000_000_000),
                    },
                }
            },
        )

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_op_stack_finality("INK")
    assert result.chain == "INK"
    assert result.unsafe_to_safe_blocks == 100
    assert result.safe_to_finalized_blocks == 100
    assert result.height_correlation == 200
    assert result.time_to_hard_finality == 100


async def test_fetch_op_stack_finality_raises_without_url(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, ink_url="")
    with pytest.raises(RuntimeError, match="no RPC URL configured"):
        await activities.fetch_op_stack_finality("INK")


# ---------------------------------------------------------------------------
# Polygon
# ---------------------------------------------------------------------------


async def test_fetch_polygon_finality_happy_path(mocker: MockerFixture) -> None:
    _patch_rpc(mocker, polygon_urls=["https://poly.example"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        if b'"id":1' in body:
            return httpx.Response(200, json=_eth_block_json(1_000, 1_000_000_100))
        if b'"id":2' in body:
            return httpx.Response(200, json=_eth_block_json(900, 1_000_000_000))
        raise AssertionError(body)

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_polygon_finality()
    assert result.latest_height == 1000
    assert result.finalized_height == 900
    assert result.latest_to_finalized_blocks == 100


# ---------------------------------------------------------------------------
# Solana
# ---------------------------------------------------------------------------


async def test_fetch_solana_finality_happy_path(mocker: MockerFixture) -> None:
    _patch_rpc(mocker, solana_urls=["https://sol.example"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        if b'"id":1' in body:
            return httpx.Response(200, json={"result": 1000})
        if b'"id":2' in body:
            return httpx.Response(200, json={"result": 990})
        if b'"id":3' in body:
            return httpx.Response(200, json={"result": 950})
        raise AssertionError(body)

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_solana_finality()
    assert result.processed_slot == 1000
    assert result.confirmed_slot == 990
    assert result.finalized_slot == 950
    assert result.confirmed_finalized_gap == 40
    assert result.processed_confirmed_gap == 10


async def test_fetch_solana_finality_raises_when_all_nodes_fail(
    mocker: MockerFixture,
) -> None:
    _patch_rpc(mocker, solana_urls=["https://sol.example"])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _install_transport(mocker, httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="all RPC nodes failed"):
        await activities.fetch_solana_finality()
