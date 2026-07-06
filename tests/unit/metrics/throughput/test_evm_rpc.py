# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for the generic RPC-backed EVM throughput fetcher."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from cert_ra.metrics.throughput import evm_rpc

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio

# Default slot_seconds used by happy-path tests below; matches Ethereum.
_DEFAULT_SLOT = 12.0


def _hex(n: int) -> str:
    return hex(n)


def _block_payload(number: int, timestamp: int, tx_count: int, request_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "number": _hex(number),
            "timestamp": _hex(timestamp),
            "transactions": [f"0x{i:064x}" for i in range(tx_count)],
        },
    }


def _fee_history_payload(
    base_fees: list[int], rewards: list[list[int]], request_id: int
) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "baseFeePerGas": [_hex(v) for v in base_fees],
            "reward": [[_hex(r) for r in inner] for inner in rewards],
            "gasUsedRatio": [0.5] * (len(base_fees) - 1),
            "oldestBlock": _hex(0),
        },
    }


class _FakeTransport(httpx.AsyncBaseTransport):
    """Routes JSON-RPC requests to canned responses keyed by (method, first param)."""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        self.calls.append(payload)
        method = payload["method"]
        params = payload.get("params", [])

        if method == "eth_getBlockByNumber":
            key = ("eth_getBlockByNumber", params[0])
        else:
            key = (method, None)

        if key not in self.responses:
            return httpx.Response(500, json={"error": f"unmocked {key}"})

        body = dict(self.responses[key])
        body["id"] = payload["id"]
        return httpx.Response(200, json=body)


def _patch_client(mocker: MockerFixture, transport: _FakeTransport) -> None:
    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    mocker.patch.object(evm_rpc.httpx, "AsyncClient", side_effect=factory)


# ---------------------------------------------------------------------------
# Sampling helper
# ---------------------------------------------------------------------------


def test_sample_block_numbers_count_and_spacing() -> None:
    numbers = evm_rpc._sample_block_numbers(10_000, _DEFAULT_SLOT)  # noqa: SLF001
    assert len(numbers) == evm_rpc._TPS_SAMPLE_COUNT  # noqa: SLF001
    assert numbers[0] == 10_000
    # Evenly spaced and monotonically decreasing.
    diffs = {numbers[i] - numbers[i + 1] for i in range(len(numbers) - 1)}
    assert len(diffs) == 1
    assert diffs.pop() > 0


def test_sample_block_numbers_step_scales_with_slot_seconds() -> None:
    # Faster slot time means more blocks in the same TPS window, so a larger
    # step between sampled block numbers.
    fast_chain = evm_rpc._sample_block_numbers(1_000_000, 0.25)  # noqa: SLF001
    slow_chain = evm_rpc._sample_block_numbers(1_000_000, 12.0)  # noqa: SLF001
    fast_step = fast_chain[0] - fast_chain[1]
    slow_step = slow_chain[0] - slow_chain[1]
    assert fast_step > slow_step


def test_gas_history_blocks_clamps_at_max_for_fast_chains() -> None:
    # On Arbitrum (0.25s slots), naively asking for 1 hour of history would
    # request 14,400 blocks; the helper clamps to _GAS_HISTORY_MAX_BLOCKS so
    # the call stays within provider per-call limits.
    arbitrum_blocks = evm_rpc._gas_history_blocks(0.25)  # noqa: SLF001
    assert arbitrum_blocks == evm_rpc._GAS_HISTORY_MAX_BLOCKS  # noqa: SLF001


def test_gas_history_blocks_scales_with_slot_seconds() -> None:
    # Ethereum (12s) needs ~300 blocks for an hour of history (well under cap).
    eth_blocks = evm_rpc._gas_history_blocks(12.0)  # noqa: SLF001
    assert eth_blocks == 300


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_fetch_evm_throughput_happy_path(mocker: MockerFixture) -> None:
    slot = _DEFAULT_SLOT
    latest_number = 20_000_000
    latest_ts = 1_700_000_000
    bps_back_blocks = round(evm_rpc._BPS_WINDOW_SECONDS / slot)  # noqa: SLF001
    sample_numbers = evm_rpc._sample_block_numbers(latest_number, slot)  # noqa: SLF001
    gas_history_blocks = evm_rpc._gas_history_blocks(slot)  # noqa: SLF001

    needed_numbers = {latest_number, latest_number - bps_back_blocks, *sample_numbers}
    responses: dict = {}
    tx_per_block = 100
    for n in needed_numbers:
        offset_blocks = latest_number - n
        ts = latest_ts - offset_blocks * slot
        responses[("eth_getBlockByNumber", _hex(n))] = _block_payload(
            n, int(ts), tx_per_block, request_id=0
        )
    responses[("eth_getBlockByNumber", "latest")] = _block_payload(
        latest_number, latest_ts, tx_per_block, request_id=0
    )

    base_fees = [10 * 10**9] * (gas_history_blocks + 1)
    rewards = [[2 * 10**9]] * gas_history_blocks
    responses[("eth_feeHistory", None)] = _fee_history_payload(
        base_fees, rewards, request_id=0
    )

    transport = _FakeTransport(responses)
    _patch_client(mocker, transport)

    result = await evm_rpc.fetch_evm_throughput(
        "ETHEREUM", slot, ["https://eth-node.example"]
    )

    assert result.chain == "ETHEREUM"
    assert result.gas_price == pytest.approx(12 * 10**9)
    assert result.blocks_per_second == pytest.approx(1.0 / slot)
    expected_tps = tx_per_block * result.blocks_per_second
    assert result.transactions_per_second == pytest.approx(expected_tps)


async def test_fetch_evm_throughput_uses_caller_chain_name(
    mocker: MockerFixture,
) -> None:
    # Same happy-path setup but called with a non-Ethereum chain name; the
    # result must echo that chain rather than hard-coding ETHEREUM.
    slot = 2.0
    latest_number = 10_000
    latest_ts = 1_700_000_000
    bps_back_blocks = round(evm_rpc._BPS_WINDOW_SECONDS / slot)  # noqa: SLF001
    sample_numbers = evm_rpc._sample_block_numbers(latest_number, slot)  # noqa: SLF001
    gas_history_blocks = evm_rpc._gas_history_blocks(slot)  # noqa: SLF001

    responses: dict = {
        ("eth_getBlockByNumber", "latest"): _block_payload(
            latest_number, latest_ts, 1, 0
        ),
        ("eth_feeHistory", None): _fee_history_payload(
            [1] * (gas_history_blocks + 1), [[0]] * gas_history_blocks, 0
        ),
    }
    needed_numbers = {latest_number - bps_back_blocks, *sample_numbers[1:]}
    for n in needed_numbers:
        offset = latest_number - n
        responses[("eth_getBlockByNumber", _hex(n))] = _block_payload(
            n, int(latest_ts - offset * slot), 1, 0
        )

    _patch_client(mocker, _FakeTransport(responses))

    result = await evm_rpc.fetch_evm_throughput(
        "BASE", slot, ["https://base-node.example"]
    )
    assert result.chain == "BASE"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_fetch_evm_throughput_no_urls_raises() -> None:
    with pytest.raises(RuntimeError, match=r"\[POLYGON\]: no RPC URLs"):
        await evm_rpc.fetch_evm_throughput("POLYGON", 2.0, [])


async def test_fetch_evm_throughput_falls_through_to_second_url(
    mocker: MockerFixture,
) -> None:
    real_fetch = evm_rpc._fetch_for_url  # noqa: SLF001
    seen: list[str] = []

    async def flaky(
        client: httpx.AsyncClient,
        url: str,
        chain: str,
        slot_seconds: float,
    ) -> Any:
        seen.append(url)
        if url == "https://broken.example":
            raise RuntimeError("simulated broken url")
        return await real_fetch(client, url, chain, slot_seconds)

    # Canned responses are consumed only on the second (working) URL because
    # the first URL is short-circuited by ``flaky`` before any HTTP traffic.
    slot = _DEFAULT_SLOT
    latest_number = 20_000_000
    latest_ts = 1_700_000_000
    bps_back = latest_number - round(evm_rpc._BPS_WINDOW_SECONDS / slot)  # noqa: SLF001
    sample_numbers = evm_rpc._sample_block_numbers(latest_number, slot)  # noqa: SLF001
    gas_history_blocks = evm_rpc._gas_history_blocks(slot)  # noqa: SLF001
    responses: dict = {
        ("eth_getBlockByNumber", "latest"): _block_payload(
            latest_number, latest_ts, 1, 0
        ),
        ("eth_feeHistory", None): _fee_history_payload(
            [1] * (gas_history_blocks + 1), [[0]] * gas_history_blocks, 0
        ),
    }
    for n in {bps_back, *sample_numbers[1:]}:
        offset = latest_number - n
        responses[("eth_getBlockByNumber", _hex(n))] = _block_payload(
            n, int(latest_ts - offset * slot), 1, 0
        )
    _patch_client(mocker, _FakeTransport(responses))
    mocker.patch.object(evm_rpc, "_fetch_for_url", side_effect=flaky)

    result = await evm_rpc.fetch_evm_throughput(
        "ETHEREUM",
        slot,
        ["https://broken.example", "https://working.example"],
    )

    assert seen == ["https://broken.example", "https://working.example"]
    assert result.chain == "ETHEREUM"


async def test_fetch_evm_throughput_raises_when_all_urls_fail(
    mocker: MockerFixture,
) -> None:
    async def always_fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("nope")

    mocker.patch.object(evm_rpc, "_fetch_for_url", side_effect=always_fail)

    with pytest.raises(RuntimeError, match=r"\[ARBITRUM\]: all RPC URLs failed"):
        await evm_rpc.fetch_evm_throughput(
            "ARBITRUM", 0.25, ["https://a.example", "https://b.example"]
        )


async def test_fetch_for_url_raises_on_non_positive_bps_interval(
    mocker: MockerFixture,
) -> None:
    slot = _DEFAULT_SLOT
    latest_number = 100
    latest_ts = 1_000
    bps_back_blocks = round(evm_rpc._BPS_WINDOW_SECONDS / slot)  # noqa: SLF001
    sample_numbers = evm_rpc._sample_block_numbers(latest_number, slot)  # noqa: SLF001
    gas_history_blocks = evm_rpc._gas_history_blocks(slot)  # noqa: SLF001
    # All blocks share the same timestamp so bps_dt == 0 and the fetcher errors.
    responses: dict = {
        ("eth_getBlockByNumber", "latest"): _block_payload(
            latest_number, latest_ts, 1, 0
        ),
        ("eth_feeHistory", None): _fee_history_payload(
            [1] * (gas_history_blocks + 1), [[0]] * gas_history_blocks, 0
        ),
    }
    for n in {latest_number - bps_back_blocks, *sample_numbers}:
        responses[("eth_getBlockByNumber", _hex(n))] = _block_payload(
            n, latest_ts, 1, 0
        )

    transport = _FakeTransport(responses)
    _patch_client(mocker, transport)

    with pytest.raises(RuntimeError, match=r"\[ETHEREUM\]: all RPC URLs failed"):
        await evm_rpc.fetch_evm_throughput(
            "ETHEREUM", slot, ["https://eth-node.example"]
        )
