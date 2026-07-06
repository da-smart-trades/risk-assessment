# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for the RPC-backed governance event counter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from cert_ra.metrics.governance import rpc

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


def _block_number_payload(n: int) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "result": hex(n)}


def _logs_payload(count: int) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "result": [{"x": i} for i in range(count)]}


class _RouterTransport(httpx.AsyncBaseTransport):
    """Routes RPC calls per URL host. Each entry: ordered list of responses."""

    def __init__(self, by_host: dict[str, list[httpx.Response]]) -> None:
        self.by_host = by_host
        self.calls: dict[str, list[dict[str, Any]]] = {h: [] for h in by_host}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        payload = json.loads(request.content)
        self.calls.setdefault(host, []).append(payload)
        responses = self.by_host.get(host, [])
        if not responses:
            return httpx.Response(500, json={"error": f"unmocked host {host}"})
        return responses.pop(0)


def _patch_client(mocker: MockerFixture, transport: _RouterTransport) -> None:
    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    mocker.patch.object(rpc.httpx, "AsyncClient", side_effect=factory)


# ---------------------------------------------------------------------------
# Empty URL list
# ---------------------------------------------------------------------------


async def test_count_evm_events_returns_zero_when_no_urls() -> None:
    count = await rpc.count_evm_events(
        urls=[], address="0x0", topics=None, lookback_blocks=100, label="t"
    )
    assert count == 0


# ---------------------------------------------------------------------------
# Single-URL happy path with chunking
# ---------------------------------------------------------------------------


async def test_count_evm_events_chunks_and_sums(mocker: MockerFixture) -> None:
    # Latest = 25_000; lookback = 25_000 → fromBlock=0, toBlock=25_000.
    # _MAX_CHUNK_BLOCKS = 10_000, so chunks are [0..9999], [10000..19999],
    # [20000..25000] = 3 calls.
    transport = _RouterTransport(
        {
            "good.example": [
                httpx.Response(200, json=_block_number_payload(25_000)),
                httpx.Response(200, json=_logs_payload(2)),
                httpx.Response(200, json=_logs_payload(3)),
                httpx.Response(200, json=_logs_payload(7)),
            ]
        }
    )
    _patch_client(mocker, transport)

    count = await rpc.count_evm_events(
        urls=["https://good.example"],
        address="0xabc",
        topics=None,
        lookback_blocks=25_000,
        label="t",
    )

    assert count == 12
    methods = [c["method"] for c in transport.calls["good.example"]]
    assert methods == [
        "eth_blockNumber",
        "eth_getLogs",
        "eth_getLogs",
        "eth_getLogs",
    ]


async def test_count_evm_events_passes_topics_filter(mocker: MockerFixture) -> None:
    transport = _RouterTransport(
        {
            "good.example": [
                httpx.Response(200, json=_block_number_payload(500)),
                httpx.Response(200, json=_logs_payload(0)),
            ]
        }
    )
    _patch_client(mocker, transport)

    topics = [["0xaaa", "0xbbb"]]
    await rpc.count_evm_events(
        urls=["https://good.example"],
        address="0xabc",
        topics=topics,
        lookback_blocks=200,
        label="t",
    )

    logs_call = transport.calls["good.example"][1]
    filter_obj = logs_call["params"][0]
    assert filter_obj["topics"] == topics
    assert filter_obj["address"] == "0xabc"
    # fromBlock = max(0, 500-200) = 300; toBlock = 500.
    assert int(filter_obj["fromBlock"], 16) == 300
    assert int(filter_obj["toBlock"], 16) == 500


# ---------------------------------------------------------------------------
# URL fallback
# ---------------------------------------------------------------------------


async def test_count_evm_events_falls_through_to_second_url(
    mocker: MockerFixture,
) -> None:
    transport = _RouterTransport(
        {
            "broken.example": [httpx.Response(500, json={"error": "boom"})],
            "good.example": [
                httpx.Response(200, json=_block_number_payload(100)),
                httpx.Response(200, json=_logs_payload(4)),
            ],
        }
    )
    _patch_client(mocker, transport)

    count = await rpc.count_evm_events(
        urls=["https://broken.example", "https://good.example"],
        address="0xabc",
        topics=None,
        lookback_blocks=100,
        label="t",
    )

    assert count == 4
    assert len(transport.calls["broken.example"]) == 1
    assert len(transport.calls["good.example"]) == 2


async def test_count_evm_events_raises_when_all_urls_fail(
    mocker: MockerFixture,
) -> None:
    transport = _RouterTransport(
        {
            "a.example": [httpx.Response(500, json={"error": "boom"})],
            "b.example": [httpx.Response(503, json={"error": "boom"})],
        }
    )
    _patch_client(mocker, transport)

    with pytest.raises(RuntimeError, match="all RPC URLs failed"):
        await rpc.count_evm_events(
            urls=["https://a.example", "https://b.example"],
            address="0xabc",
            topics=None,
            lookback_blocks=10,
            label="t",
        )
