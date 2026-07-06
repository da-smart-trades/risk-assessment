# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""RPC-backed throughput fetcher for EVM chains.

Parametrized over ``(chain, slot_seconds, urls)`` so a single fetcher serves
Ethereum, Arbitrum, Base, Polygon, Optimism, Avalanche C-Chain, Ink, and
Unichain. Per-chain knobs (slot/block time, URL list) are passed in by the
dispatcher in ``activities.py``.

* ``gas_price`` — mean effective gas price over the recent fee-history window,
  computed as ``mean(baseFeePerGas) + mean(reward[P50])`` from
  ``eth_feeHistory``. Matches what an hourly Dune ``avg(gas_price)`` would
  return on a healthy chain.
* ``blocks_per_second`` — block-number delta divided by timestamp delta
  between the head and a block ``_BPS_WINDOW_SECONDS`` seconds in the past.
* ``transactions_per_second`` — ``_TPS_SAMPLE_COUNT`` blocks sampled evenly
  across the past ``_TPS_WINDOW_SECONDS``; ``mean(txs/block) * blocks/sec``
  gives a network-wide TPS estimate (sampling only a few blocks within a long
  window would otherwise undercount by ``total_blocks / sample_count``).
"""

from __future__ import annotations

import asyncio
from statistics import mean

import httpx
from pydantic import BaseModel, Field
from temporalio import activity

from cert_ra.utils import HexInt  # noqa: TC001

from .schemas import ThroughputResult

# --- Tuning knobs ----------------------------------------------------------
# Number of blocks sampled per fetch to estimate transactions/sec.
_TPS_SAMPLE_COUNT = 10
# Total time window that the TPS samples are spread across (one hour).
_TPS_WINDOW_SECONDS = 3600
# Lookback window for the blocks/sec measurement.
_BPS_WINDOW_SECONDS = 300
# Target time-span (in seconds) for ``eth_feeHistory``. Translated to a block
# count per chain by dividing by ``slot_seconds``. Capped to stay under
# provider ``blockCount`` limits (most providers reject > 1024).
_GAS_HISTORY_TARGET_SECONDS = 3600
_GAS_HISTORY_MAX_BLOCKS = 1024
# Percentile used for the priority-fee component of effective gas price.
_PRIORITY_FEE_PERCENTILE = 50
# HTTP timeout for each RPC call.
_RPC_TIMEOUT_SECONDS = 30.0
# ---------------------------------------------------------------------------


class _Block(BaseModel):
    number: HexInt
    timestamp: HexInt
    transactions: list[str] = []


class _BlockResponse(BaseModel):
    result: _Block | None = None


class _FeeHistory(BaseModel):
    base_fee_per_gas: list[HexInt] = Field(alias="baseFeePerGas")
    reward: list[list[HexInt]] = Field(default_factory=list)


class _FeeHistoryResponse(BaseModel):
    result: _FeeHistory | None = None


def _gas_history_blocks(slot_seconds: float) -> int:
    """Block count for ``eth_feeHistory`` that covers ~``_GAS_HISTORY_TARGET_SECONDS``.

    Clamped to ``_GAS_HISTORY_MAX_BLOCKS`` so the call stays within provider
    limits even on sub-second slot chains.
    """
    blocks = max(1, round(_GAS_HISTORY_TARGET_SECONDS / slot_seconds))
    return min(blocks, _GAS_HISTORY_MAX_BLOCKS)


async def _rpc_post(
    client: httpx.AsyncClient,
    url: str,
    method: str,
    params: list[object],
    request_id: int,
) -> bytes:
    response = await client.post(
        url,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": request_id},
    )
    response.raise_for_status()
    return response.content


def _sample_block_numbers(latest: int, slot_seconds: float) -> list[int]:
    """Block numbers to sample for TPS, evenly spaced across the TPS window."""
    total_back_blocks = max(
        _TPS_SAMPLE_COUNT, round(_TPS_WINDOW_SECONDS / slot_seconds)
    )
    step = max(1, total_back_blocks // _TPS_SAMPLE_COUNT)
    return [latest - i * step for i in range(_TPS_SAMPLE_COUNT)]


async def _fetch_for_url(
    client: httpx.AsyncClient,
    url: str,
    chain: str,
    slot_seconds: float,
) -> ThroughputResult:
    """Run all throughput queries against one RPC URL and assemble a result."""
    bps_back_blocks = max(1, round(_BPS_WINDOW_SECONDS / slot_seconds))
    gas_history_blocks = _gas_history_blocks(slot_seconds)

    latest_raw, fee_raw = await asyncio.gather(
        _rpc_post(client, url, "eth_getBlockByNumber", ["latest", False], 1),
        _rpc_post(
            client,
            url,
            "eth_feeHistory",
            [hex(gas_history_blocks), "latest", [_PRIORITY_FEE_PERCENTILE]],
            2,
        ),
    )
    latest_resp = _BlockResponse.model_validate_json(latest_raw)
    fee_resp = _FeeHistoryResponse.model_validate_json(fee_raw)
    if latest_resp.result is None or fee_resp.result is None:
        msg = f"evm_throughput[{chain}]: empty RPC response from {url}"
        raise RuntimeError(msg)

    latest_block = latest_resp.result
    fee_history = fee_resp.result

    # eth_feeHistory returns blockCount + 1 baseFee entries; the trailing one is
    # the projected next block. Drop it before averaging.
    base_fees = fee_history.base_fee_per_gas[:-1] or fee_history.base_fee_per_gas
    avg_base_fee = mean(base_fees)
    rewards = [r[0] for r in fee_history.reward if r]
    avg_priority_fee: float = mean(rewards) if rewards else 0.0
    gas_price = float(avg_base_fee + avg_priority_fee)

    bps_back_number = latest_block.number - bps_back_blocks
    sample_numbers = _sample_block_numbers(latest_block.number, slot_seconds)
    # ``latest`` is already fetched; deduplicate against the BPS back-block.
    extra_numbers = sorted({bps_back_number, *sample_numbers[1:]})
    fetched = await asyncio.gather(
        *[
            _rpc_post(client, url, "eth_getBlockByNumber", [hex(n), False], 100 + i)
            for i, n in enumerate(extra_numbers)
        ]
    )
    blocks_by_number: dict[int, _Block] = {latest_block.number: latest_block}
    for n, raw in zip(extra_numbers, fetched, strict=True):
        block = _BlockResponse.model_validate_json(raw).result
        if block is None:
            msg = f"evm_throughput[{chain}]: missing block {n} from {url}"
            raise RuntimeError(msg)
        blocks_by_number[n] = block

    bps_dt = latest_block.timestamp - blocks_by_number[bps_back_number].timestamp
    if bps_dt <= 0:
        msg = (
            f"evm_throughput[{chain}]: non-positive BPS interval ({bps_dt}s) from {url}"
        )
        raise RuntimeError(msg)
    blocks_per_second = bps_back_blocks / bps_dt

    tps_blocks = [blocks_by_number[n] for n in sample_numbers]
    tps_dt = tps_blocks[0].timestamp - tps_blocks[-1].timestamp
    if tps_dt <= 0:
        msg = (
            f"evm_throughput[{chain}]: non-positive TPS interval ({tps_dt}s) from {url}"
        )
        raise RuntimeError(msg)
    # Extrapolate from sampled blocks: ``mean(txs/block) * blocks/sec`` gives a
    # network-wide TPS estimate. Dividing the sampled tx total by ``tps_dt``
    # would only count txs in the sampled blocks against the full window —
    # undercounting by ``total_blocks / sample_count``.
    avg_txs_per_block = mean(len(b.transactions) for b in tps_blocks)
    transactions_per_second = avg_txs_per_block * blocks_per_second

    return ThroughputResult(
        chain=chain,
        gas_price=gas_price,
        transactions_per_second=transactions_per_second,
        blocks_per_second=blocks_per_second,
    )


async def fetch_evm_throughput(
    chain: str,
    slot_seconds: float,
    urls: list[str],
) -> ThroughputResult:
    """Fetch gas price, TPS, and BPS for an EVM chain from JSON-RPC.

    Tries each ``url`` in order; raises ``RuntimeError`` if every URL fails
    (which triggers Temporal retry).
    """
    if not urls:
        msg = f"evm_throughput[{chain}]: no RPC URLs configured"
        raise RuntimeError(msg)

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_RPC_TIMEOUT_SECONDS) as client:
        for url in urls:
            try:
                return await _fetch_for_url(client, url, chain, slot_seconds)
            except Exception as exc:  # noqa: BLE001
                activity.logger.warning(
                    f"evm_throughput[{chain}]: RPC failed url={url} error={exc}"
                )
                last_exc = exc

    msg = f"evm_throughput[{chain}]: all RPC URLs failed"
    raise RuntimeError(msg) from last_exc
