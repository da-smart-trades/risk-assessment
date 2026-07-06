# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""EVM ``eth_getLogs`` event-count helpers used by Arbitrum/Base governance.

Counts log events emitted from a specific contract within a fixed lookback
window of blocks, chunked to stay under provider per-call block-range limits.

Each governance workflow runs every ``_GOVERNANCE_INTERVAL`` (6h), so the
lookback is sized to cover that window per-chain. The result is an estimate of
"events in the last poll window" rather than a strict cumulative delta — this
avoids needing a per-row cursor column.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel
from temporalio import activity

from cert_ra.utils import HexInt  # noqa: TC001

# --- Tuning knobs ----------------------------------------------------------
# Maximum ``toBlock - fromBlock + 1`` per ``eth_getLogs`` call. Most providers
# cap somewhere between 1k and 10k for unindexed scans.
_MAX_CHUNK_BLOCKS = 10_000
# HTTP timeout for each RPC call.
_RPC_TIMEOUT_SECONDS = 30.0
# ---------------------------------------------------------------------------


class _BlockNumberResponse(BaseModel):
    result: HexInt | None = None


class _LogsResponse(BaseModel):
    # ``result`` is a list of log objects whose shape we don't need to parse —
    # we only count entries.
    result: list[object] | None = None


async def _eth_block_number(client: httpx.AsyncClient, url: str) -> int:
    response = await client.post(
        url,
        json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
    )
    response.raise_for_status()
    parsed = _BlockNumberResponse.model_validate_json(response.content)
    if parsed.result is None:
        msg = f"governance_rpc: eth_blockNumber returned null from {url}"
        raise RuntimeError(msg)
    return parsed.result


async def _eth_log_count(
    client: httpx.AsyncClient,
    url: str,
    address: str,
    topics: list[list[str]] | None,
    from_block: int,
    to_block: int,
) -> int:
    filter_params: dict[str, object] = {
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "address": address,
    }
    if topics is not None:
        filter_params["topics"] = topics

    response = await client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "method": "eth_getLogs",
            "params": [filter_params],
            "id": 1,
        },
    )
    response.raise_for_status()
    parsed = _LogsResponse.model_validate_json(response.content)
    return len(parsed.result or [])


async def _count_for_url(
    client: httpx.AsyncClient,
    url: str,
    address: str,
    topics: list[list[str]] | None,
    lookback_blocks: int,
) -> int:
    latest = await _eth_block_number(client, url)
    from_block = max(0, latest - lookback_blocks)

    # Chunk into ranges of <= _MAX_CHUNK_BLOCKS so providers don't reject the
    # call. Sequential to keep concurrency low against a single endpoint.
    total = 0
    cursor = from_block
    while cursor <= latest:
        chunk_end = min(latest, cursor + _MAX_CHUNK_BLOCKS - 1)
        total += await _eth_log_count(client, url, address, topics, cursor, chunk_end)
        cursor = chunk_end + 1
    return total


async def count_evm_events(
    urls: list[str],
    address: str,
    topics: list[list[str]] | None,
    lookback_blocks: int,
    *,
    label: str,
) -> int:
    """Count log events from ``address`` over the last ``lookback_blocks`` blocks.

    Args:
        urls: RPC URLs to try in order. If empty, returns 0 with a warning.
        address: Contract address whose logs to filter for.
        topics: ``eth_getLogs`` ``topics`` filter (or ``None`` for all events).
        lookback_blocks: Number of blocks counted back from the chain tip.
        label: Short identifier used in log messages (e.g. ``"arb_timelock"``).

    Returns:
        Total event count across the lookback window.

    Raises:
        RuntimeError: every URL failed (signals Temporal to retry).
    """
    if not urls:
        activity.logger.warning(
            f"governance_rpc: no RPC URLs configured for {label}; returning 0"
        )
        return 0

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_RPC_TIMEOUT_SECONDS) as client:
        for url in urls:
            try:
                return await _count_for_url(
                    client, url, address, topics, lookback_blocks
                )
            except (TimeoutError, httpx.HTTPError, RuntimeError) as exc:
                activity.logger.warning(
                    f"governance_rpc: RPC failed label={label} url={url} error={exc}"
                )
                last_exc = exc

    msg = f"governance_rpc: all RPC URLs failed for {label}"
    raise RuntimeError(msg) from last_exc
