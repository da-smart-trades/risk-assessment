# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for the on-chain ``totalSupply`` JSON-RPC fetchers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from cert_ra.metrics.tokens import supply_rpc

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


class _FakeTransport(httpx.AsyncBaseTransport):
    """Routes JSON-RPC requests to canned responses keyed by ``(url, method)``.

    Each response slot is a list — the first element is popped on each call, so
    tests can stage a failure followed by a success for fallback scenarios.
    """

    def __init__(self, responses: dict[tuple[str, str], list[Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        url = str(request.url)
        self.calls.append((url, payload))
        key = (url, payload["method"])
        if key not in self.responses or not self.responses[key]:
            return httpx.Response(500, json={"error": f"unmocked {key}"})
        body = self.responses[key].pop(0)
        if isinstance(body, Exception):
            raise body
        body = dict(body)
        body["id"] = payload["id"]
        return httpx.Response(200, json=body)


def _patch_client(mocker: MockerFixture, transport: _FakeTransport) -> None:
    real_client = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    mocker.patch.object(supply_rpc.httpx, "AsyncClient", side_effect=factory)


# ---------------------------------------------------------------------------
# EVM
# ---------------------------------------------------------------------------


async def test_evm_total_supply_scales_by_decimals(mocker: MockerFixture) -> None:
    url = "https://rpc.example/eth"
    raw_supply = 12_345_678_900_000  # 12,345,678.9 USDC at 6 decimals
    transport = _FakeTransport(
        {
            (url, "eth_call"): [
                {"jsonrpc": "2.0", "result": hex(raw_supply)},
            ]
        }
    )
    _patch_client(mocker, transport)

    value = await supply_rpc.fetch_evm_total_supply(
        chain="ETHEREUM",
        contract_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        decimals=6,
        urls=[url],
    )

    assert value == pytest.approx(12_345_678.9)
    # Verifies the canonical totalSupply() selector and target contract land
    # in the eth_call payload — the selector is hard-coded inside the fetcher.
    _, payload = transport.calls[0]
    assert payload["method"] == "eth_call"
    assert payload["params"][0]["data"] == "0x18160ddd"
    assert payload["params"][0]["to"] == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    assert payload["params"][1] == "latest"


async def test_evm_total_supply_falls_back_on_failure(mocker: MockerFixture) -> None:
    bad_url = "https://rpc.example/bad"
    good_url = "https://rpc.example/good"
    transport = _FakeTransport(
        {
            (bad_url, "eth_call"): [httpx.ConnectError("boom")],
            (good_url, "eth_call"): [{"jsonrpc": "2.0", "result": hex(1_000_000)}],
        }
    )
    _patch_client(mocker, transport)

    value = await supply_rpc.fetch_evm_total_supply(
        chain="BASE",
        contract_address="0x0000000000000000000000000000000000000001",
        decimals=6,
        urls=[bad_url, good_url],
    )

    assert value == pytest.approx(1.0)
    # The fallback ordering is observable in the call log.
    assert [u for u, _ in transport.calls] == [bad_url, good_url]


async def test_evm_total_supply_raises_when_all_urls_fail(
    mocker: MockerFixture,
) -> None:
    url1, url2 = "https://rpc.example/a", "https://rpc.example/b"
    transport = _FakeTransport(
        {
            (url1, "eth_call"): [httpx.ConnectError("a down")],
            (url2, "eth_call"): [httpx.ConnectError("b down")],
        }
    )
    _patch_client(mocker, transport)

    with pytest.raises(RuntimeError, match="all RPC URLs failed"):
        await supply_rpc.fetch_evm_total_supply(
            chain="ETHEREUM",
            contract_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            decimals=6,
            urls=[url1, url2],
        )


async def test_evm_total_supply_requires_urls() -> None:
    with pytest.raises(RuntimeError, match="no RPC URLs configured"):
        await supply_rpc.fetch_evm_total_supply(
            chain="ETHEREUM",
            contract_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            decimals=6,
            urls=[],
        )


async def test_evm_total_supply_treats_empty_result_as_failure(
    mocker: MockerFixture,
) -> None:
    url = "https://rpc.example/eth"
    transport = _FakeTransport(
        {(url, "eth_call"): [{"jsonrpc": "2.0", "result": "0x"}]}
    )
    _patch_client(mocker, transport)

    with pytest.raises(RuntimeError, match="all RPC URLs failed"):
        await supply_rpc.fetch_evm_total_supply(
            chain="ETHEREUM",
            contract_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            decimals=6,
            urls=[url],
        )


# ---------------------------------------------------------------------------
# Solana
# ---------------------------------------------------------------------------


async def test_solana_total_supply_uses_raw_amount(mocker: MockerFixture) -> None:
    url = "https://rpc.example/solana"
    # 9,876,543.21 USDC at 6 decimals — passed as a string to preserve precision
    # since uiAmount is a float and would lose digits on large supplies.
    transport = _FakeTransport(
        {
            (url, "getTokenSupply"): [
                {
                    "jsonrpc": "2.0",
                    "result": {
                        "value": {
                            "amount": "9876543210000",
                            "decimals": 6,
                            "uiAmount": 9876543.21,
                            "uiAmountString": "9876543.21",
                        },
                    },
                }
            ]
        }
    )
    _patch_client(mocker, transport)

    value = await supply_rpc.fetch_solana_total_supply(
        mint_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        decimals=6,
        urls=[url],
    )

    assert value == pytest.approx(9_876_543.21)
    _, payload = transport.calls[0]
    assert payload["method"] == "getTokenSupply"
    assert payload["params"] == ["EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"]


async def test_solana_total_supply_falls_back_on_failure(
    mocker: MockerFixture,
) -> None:
    bad_url = "https://rpc.example/bad"
    good_url = "https://rpc.example/good"
    transport = _FakeTransport(
        {
            (bad_url, "getTokenSupply"): [httpx.ConnectError("boom")],
            (good_url, "getTokenSupply"): [
                {
                    "jsonrpc": "2.0",
                    "result": {
                        "value": {
                            "amount": "1000000",
                            "decimals": 6,
                            "uiAmount": 1.0,
                            "uiAmountString": "1",
                        },
                    },
                }
            ],
        }
    )
    _patch_client(mocker, transport)

    value = await supply_rpc.fetch_solana_total_supply(
        mint_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        decimals=6,
        urls=[bad_url, good_url],
    )

    assert value == pytest.approx(1.0)
    assert [u for u, _ in transport.calls] == [bad_url, good_url]


async def test_solana_total_supply_raises_when_all_urls_fail(
    mocker: MockerFixture,
) -> None:
    url = "https://rpc.example/solana"
    transport = _FakeTransport({(url, "getTokenSupply"): [httpx.ConnectError("down")]})
    _patch_client(mocker, transport)

    with pytest.raises(RuntimeError, match="all RPC URLs failed"):
        await supply_rpc.fetch_solana_total_supply(
            mint_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            decimals=6,
            urls=[url],
        )


async def test_solana_total_supply_requires_urls() -> None:
    with pytest.raises(RuntimeError, match="no RPC URLs configured"):
        await supply_rpc.fetch_solana_total_supply(
            mint_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            decimals=6,
            urls=[],
        )
