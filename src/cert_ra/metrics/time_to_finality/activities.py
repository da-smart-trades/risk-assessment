# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import itertools
import time
from typing import TYPE_CHECKING

from solana.rpc import websocket_api
from temporalio import activity
from web3 import AsyncWeb3
from web3.providers.persistent import WebSocketProvider

from cert_ra.db.models import TimeToFinality
from cert_ra.metrics._session import session_factory
from cert_ra.settings.rpc import get_rpc_settings
from cert_ra.types import ChainType

from .schemas import CHAIN_SUBSCRIPTIONS, TimeToFinalityResult

if TYPE_CHECKING:
    from collections.abc import Sequence

# Number of head/slot notifications to observe before averaging the gap.
_NUM_MESSAGES = 3


def _to_ws(url: str) -> str:
    """Convert an HTTP(S) RPC URL to the matching ``ws(s)://`` form."""
    if url.startswith("https://"):
        return "wss://" + url[len("https://") :]
    if url.startswith("http://"):
        return "ws://" + url[len("http://") :]
    return url


def _urls_for_chain(chain: ChainType) -> Sequence[str]:
    """Return the list of RPC URLs to try for a chain, ordered by preference."""
    rpc = get_rpc_settings()
    match chain:
        case ChainType.ETHEREUM:
            return rpc.ethereum_urls
        case ChainType.BASE:
            return rpc.base_urls
        case ChainType.SOLANA:
            return rpc.solana_urls
        case ChainType.INK:
            return [rpc.ink_url] if rpc.ink_url else []
        case ChainType.UNICHAIN:
            return [rpc.unichain_url] if rpc.unichain_url else []
        case _:
            return []


async def _eth_soft_finality(ws_url: str, method: str) -> float:
    """Average seconds between EVM head/flashblock notifications.

    ``method`` is passed verbatim to ``eth_subscribe`` (e.g. ``"newHeads"`` or
    ``"newFlashblocks"``).
    """
    async with AsyncWeb3(WebSocketProvider(ws_url)) as w3:
        subscription_id = await w3.eth.subscribe(method)
        timestamps: list[float] = []
        try:
            async for _ in w3.socket.process_subscriptions():
                timestamps.append(time.monotonic())
                if len(timestamps) >= _NUM_MESSAGES:
                    break
        finally:
            await w3.eth.unsubscribe(subscription_id)

    return _mean_gap(timestamps)


async def _solana_soft_finality(ws_url: str) -> float:
    """Average seconds between Solana slot notifications."""
    async with websocket_api.connect(ws_url) as websocket:
        await websocket.slot_subscribe()
        first = await websocket.recv()
        subscription_id = first[0].result
        if not isinstance(subscription_id, int):
            msg = (
                f"solana_time_to_finality: invalid subscription id {subscription_id!r}"
            )
            raise TypeError(msg)

        try:
            timestamps: list[float] = []
            for _ in range(_NUM_MESSAGES):
                await websocket.recv()
                timestamps.append(time.monotonic())
        finally:
            await websocket.slot_unsubscribe(subscription_id)

    return _mean_gap(timestamps)


def _mean_gap(timestamps: list[float]) -> float:
    if len(timestamps) < 2:  # noqa: PLR2004
        msg = "time_to_finality: not enough messages received to compute an average"
        raise RuntimeError(msg)
    diffs = [b - a for a, b in itertools.pairwise(timestamps)]
    return sum(diffs) / len(diffs)


@activity.defn
async def fetch_time_to_finality(chain: str) -> TimeToFinalityResult:
    """Fetch soft-finality (average time between new heads/slots) for a chain."""
    chain_upper = chain.upper()
    kind = CHAIN_SUBSCRIPTIONS.get(chain_upper)
    if kind is None:
        msg = f"time_to_finality: chain {chain_upper} not supported"
        raise ValueError(msg)

    chain_type = ChainType(chain_upper)
    urls = _urls_for_chain(chain_type)
    if not urls:
        msg = f"time_to_finality: no RPC URL configured for {chain_upper}"
        raise RuntimeError(msg)

    last_exc: Exception | None = None
    for url in urls:
        ws_url = _to_ws(url)
        try:
            if kind == "solana_slot":
                gap = await _solana_soft_finality(ws_url)
            else:
                method = "newHeads" if kind == "eth_heads" else "newFlashblocks"
                gap = await _eth_soft_finality(ws_url, method)
        except Exception as exc:  # noqa: BLE001
            activity.logger.warning(
                f"time_to_finality: websocket node failed "
                f"chain={chain_upper} url={url} error={exc}"
            )
            last_exc = exc
            continue
        else:
            return TimeToFinalityResult(chain=chain_upper, soft_finality_seconds=gap)

    msg = f"time_to_finality: all websocket nodes failed for {chain_upper}"
    raise RuntimeError(msg) from last_exc


@activity.defn
async def store_time_to_finality(result: TimeToFinalityResult) -> None:
    """Persist a time-to-finality snapshot to the database."""
    async with session_factory()() as session:
        session.add(
            TimeToFinality(
                chain=ChainType(result.chain),
                soft_finality_seconds=result.soft_finality_seconds,
            )
        )
        await session.commit()
