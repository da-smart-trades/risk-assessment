# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import asyncio
import time
from functools import cache

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity

from cert_ra.db.engine_factory import create_sqlalchemy_engine
from cert_ra.db.models.finality import (
    FinalityEthereum,
    FinalityEvmL2,
    FinalityOpStack,
    FinalityPolygon,
    FinalitySolana,
)
from cert_ra.settings.rpc import get_rpc_settings
from cert_ra.types import ChainType
from cert_ra.utils import HexInt  # noqa: TC001

from .schemas import (
    EthFinalityResult,
    EvmL2FinalityResult,
    OPStackFinalityResult,
    PolygonFinalityResult,
    SolanaFinalityResult,
)

# ---------------------------------------------------------------------------
# JSON-RPC response parsing models (private)
# ---------------------------------------------------------------------------


class _EthBlock(BaseModel):
    number: HexInt
    timestamp: HexInt


class _EthBlockResponse(BaseModel):
    result: _EthBlock | None = None


class _BeaconCheckpoint(BaseModel):
    # Beacon epoch arrives as a decimal string, e.g. "12345"; Pydantic lax mode coerces it.
    epoch: int


class _BeaconCheckpoints(BaseModel):
    current_justified: _BeaconCheckpoint
    finalized: _BeaconCheckpoint


class _BeaconFinalityResponse(BaseModel):
    data: _BeaconCheckpoints


class _SolanaSlotResponse(BaseModel):
    result: int


class _SyncBlock(BaseModel):
    number: HexInt
    timestamp: HexInt


class _OPStackSyncStatusResult(BaseModel):
    unsafe_l2: _SyncBlock
    safe_l2: _SyncBlock
    finalized_l2: _SyncBlock


class _OPStackSyncStatusResponse(BaseModel):
    result: _OPStackSyncStatusResult


# ---------------------------------------------------------------------------
# Ethereum finality constants
# ---------------------------------------------------------------------------

_GENESIS_TIMESTAMP = 1_606_824_023
_SLOTS_PER_EPOCH = 32
_SECONDS_PER_SLOT = 12


def _time_since_finality_advance(finalized_epoch: int) -> float:
    """Seconds elapsed since the first slot of epoch ``finalized_epoch + 1`` was due."""
    epoch_start_slot = (finalized_epoch + 1) * _SLOTS_PER_EPOCH
    slot_time = _GENESIS_TIMESTAMP + epoch_start_slot * _SECONDS_PER_SLOT
    return time.time() - slot_time


# ---------------------------------------------------------------------------
# Database session factory
# ---------------------------------------------------------------------------


@cache
def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(create_sqlalchemy_engine(), expire_on_commit=False)


# ---------------------------------------------------------------------------
# Fetch activities
# ---------------------------------------------------------------------------


@activity.defn
async def fetch_ethereum_finality() -> EthFinalityResult:
    """Fetch Ethereum finality from the execution layer and beacon chain.

    Tries each configured URL in order. Raises ``RuntimeError`` when all
    execution-layer nodes fail (triggers Temporal retry). Beacon failures
    fall back to ``-1`` sentinel values for epoch fields.
    """
    rpc = get_rpc_settings()

    head_block = finalized_block = safe_block = None
    async with httpx.AsyncClient() as client:
        for url in rpc.ethereum_urls:
            try:
                head_r, fin_r, safe_r = await asyncio.gather(
                    client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "eth_getBlockByNumber",
                            "params": ["latest", False],
                            "id": 1,
                        },
                    ),
                    client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "eth_getBlockByNumber",
                            "params": ["finalized", False],
                            "id": 2,
                        },
                    ),
                    client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "eth_getBlockByNumber",
                            "params": ["safe", False],
                            "id": 3,
                        },
                    ),
                )
                head = _EthBlockResponse.model_validate_json(head_r.content)
                fin = _EthBlockResponse.model_validate_json(fin_r.content)
                safe = _EthBlockResponse.model_validate_json(safe_r.content)
                if head.result and fin.result and safe.result:
                    head_block = head.result
                    finalized_block = fin.result
                    safe_block = safe.result
                    break
            except Exception as exc:  # noqa: BLE001
                activity.logger.warning(
                    f"eth_finality: execution-layer node failed url={url} error={exc}"
                )

        if not head_block or not finalized_block or not safe_block:
            msg = "eth_finality: all execution-layer RPC nodes failed"
            raise RuntimeError(msg)

        justified_epoch = finalized_epoch = -1
        for url in rpc.ethereum_urls:
            try:
                beacon_r = await client.get(
                    f"{url}/eth/v1/beacon/states/head/finality_checkpoints",
                )
                beacon_r.raise_for_status()
                beacon = _BeaconFinalityResponse.model_validate_json(beacon_r.content)
                justified_epoch = beacon.data.current_justified.epoch
                finalized_epoch = beacon.data.finalized.epoch
                break
            except Exception as exc:  # noqa: BLE001
                activity.logger.warning(
                    f"eth_finality: beacon API failed url={url} error={exc}"
                )

    gap = max(0, justified_epoch - finalized_epoch) if finalized_epoch >= 0 else -1
    finality_advance = (
        _time_since_finality_advance(finalized_epoch) if finalized_epoch >= 0 else -1.0
    )

    return EthFinalityResult(
        head_height=head_block.number,
        finalized_height=finalized_block.number,
        safe_height=safe_block.number,
        justified_epoch=justified_epoch,
        finalized_epoch=finalized_epoch,
        justified_finalized_gap=gap,
        time_since_finality_advance=finality_advance,
        head_to_finalized_time=head_block.timestamp - finalized_block.timestamp,
    )


@activity.defn
async def fetch_evm_l2_finality(chain: str) -> EvmL2FinalityResult:
    """Fetch standard EVM L2 finality (latest / safe / finalized).

    ``chain`` must be one of ``"ARBITRUM"``, ``"BASE"``, or ``"OPTIMISM"``.
    Base omits ``height_correlation`` and ``time_to_hard_finality`` (set to ``None``).
    """
    rpc = get_rpc_settings()
    chain_upper = chain.upper()
    urls_by_chain = {
        "ARBITRUM": rpc.arbitrum_urls,
        "BASE": rpc.base_urls,
        "OPTIMISM": rpc.optimism_urls,
    }
    urls = urls_by_chain[chain_upper]
    is_base = chain_upper == "BASE"

    async with httpx.AsyncClient() as client:
        for url in urls:
            try:
                latest_r, safe_r, fin_r = await asyncio.gather(
                    client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "eth_getBlockByNumber",
                            "params": ["latest", False],
                            "id": 1,
                        },
                    ),
                    client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "eth_getBlockByNumber",
                            "params": ["safe", False],
                            "id": 2,
                        },
                    ),
                    client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "eth_getBlockByNumber",
                            "params": ["finalized", False],
                            "id": 3,
                        },
                    ),
                )
                latest = _EthBlockResponse.model_validate_json(latest_r.content)
                safe = _EthBlockResponse.model_validate_json(safe_r.content)
                fin = _EthBlockResponse.model_validate_json(fin_r.content)

                if latest.result and safe.result and fin.result:
                    latest_h = latest.result.number
                    safe_h = safe.result.number
                    fin_h = fin.result.number
                    return EvmL2FinalityResult(
                        chain=chain_upper,
                        latest_height=latest_h,
                        safe_height=safe_h,
                        finalized_height=fin_h,
                        latest_to_safe_blocks=max(0, latest_h - safe_h),
                        safe_to_finalized_blocks=max(0, safe_h - fin_h),
                        time_since_last_head=time.time() - latest.result.timestamp,
                        height_correlation=None
                        if is_base
                        else max(0, latest_h - fin_h),
                        time_to_hard_finality=None
                        if is_base
                        else (latest.result.timestamp - fin.result.timestamp),
                    )
            except Exception as exc:  # noqa: BLE001
                activity.logger.warning(
                    f"{chain.lower()}_finality: RPC node failed url={url} error={exc}"
                )

    msg = f"{chain.lower()}_finality: all RPC nodes failed"
    raise RuntimeError(msg)


@activity.defn
async def fetch_op_stack_finality(chain: str) -> OPStackFinalityResult:
    """Fetch OP Stack finality via ``optimism_syncStatus``.

    ``chain`` must be one of ``"INK"`` or ``"UNICHAIN"``.
    """
    rpc = get_rpc_settings()
    chain_upper = chain.upper()
    url = rpc.ink_url if chain_upper == "INK" else rpc.unichain_url

    if not url:
        msg = f"{chain.lower()}_finality: no RPC URL configured (set CERT_RA_RPC_INK_URL or CERT_RA_RPC_UNICHAIN_URL)"
        raise RuntimeError(msg)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={
                "jsonrpc": "2.0",
                "method": "optimism_syncStatus",
                "params": [],
                "id": 1,
            },
        )
        resp.raise_for_status()
        parsed = _OPStackSyncStatusResponse.model_validate_json(resp.content)

    r = parsed.result
    unsafe_h = r.unsafe_l2.number
    safe_h = r.safe_l2.number
    fin_h = r.finalized_l2.number

    return OPStackFinalityResult(
        chain=chain_upper,
        unsafe_height=unsafe_h,
        safe_height=safe_h,
        finalized_height=fin_h,
        unsafe_to_safe_blocks=max(0, unsafe_h - safe_h),
        safe_to_finalized_blocks=max(0, safe_h - fin_h),
        time_since_last_unsafe=time.time() - r.unsafe_l2.timestamp,
        height_correlation=max(0, unsafe_h - fin_h),
        time_to_hard_finality=r.unsafe_l2.timestamp - r.finalized_l2.timestamp,
    )


@activity.defn
async def fetch_polygon_finality() -> PolygonFinalityResult:
    """Fetch Polygon finality (latest / finalized — no safe stage)."""
    rpc = get_rpc_settings()

    async with httpx.AsyncClient() as client:
        for url in rpc.polygon_urls:
            try:
                latest_r, fin_r = await asyncio.gather(
                    client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "eth_getBlockByNumber",
                            "params": ["latest", False],
                            "id": 1,
                        },
                    ),
                    client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "eth_getBlockByNumber",
                            "params": ["finalized", False],
                            "id": 2,
                        },
                    ),
                )
                latest = _EthBlockResponse.model_validate_json(latest_r.content)
                fin = _EthBlockResponse.model_validate_json(fin_r.content)

                if latest.result and fin.result:
                    latest_h = latest.result.number
                    fin_h = fin.result.number
                    return PolygonFinalityResult(
                        latest_height=latest_h,
                        finalized_height=fin_h,
                        latest_to_finalized_blocks=max(0, latest_h - fin_h),
                        time_since_last_head=time.time() - latest.result.timestamp,
                    )
            except Exception as exc:  # noqa: BLE001
                activity.logger.warning(
                    f"polygon_finality: RPC node failed url={url} error={exc}"
                )

    msg = "polygon_finality: all RPC nodes failed"
    raise RuntimeError(msg)


@activity.defn
async def fetch_solana_finality() -> SolanaFinalityResult:
    """Fetch Solana finality from the slot commitment pipeline (processed / confirmed / finalized)."""
    rpc = get_rpc_settings()

    async with httpx.AsyncClient() as client:
        for url in rpc.solana_urls:
            try:
                proc_r, conf_r, fin_r = await asyncio.gather(
                    client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "getSlot",
                            "params": [{"commitment": "processed"}],
                            "id": 1,
                        },
                    ),
                    client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "getSlot",
                            "params": [{"commitment": "confirmed"}],
                            "id": 2,
                        },
                    ),
                    client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "getSlot",
                            "params": [{"commitment": "finalized"}],
                            "id": 3,
                        },
                    ),
                )
                processed = _SolanaSlotResponse.model_validate_json(
                    proc_r.content
                ).result
                confirmed = _SolanaSlotResponse.model_validate_json(
                    conf_r.content
                ).result
                finalized = _SolanaSlotResponse.model_validate_json(
                    fin_r.content
                ).result

                return SolanaFinalityResult(
                    processed_slot=processed,
                    confirmed_slot=confirmed,
                    finalized_slot=finalized,
                    confirmed_finalized_gap=max(0, confirmed - finalized),
                    processed_confirmed_gap=max(0, processed - confirmed),
                )
            except Exception as exc:  # noqa: BLE001
                activity.logger.warning(
                    f"sol_finality: RPC node failed url={url} error={exc}"
                )

    msg = "sol_finality: all RPC nodes failed"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Store activities
# ---------------------------------------------------------------------------


@activity.defn
async def store_ethereum_finality(result: EthFinalityResult) -> None:
    """Persist an Ethereum finality snapshot to the database."""
    async with _session_factory()() as session:
        session.add(
            FinalityEthereum(
                head_height=result.head_height,
                finalized_height=result.finalized_height,
                safe_height=result.safe_height,
                justified_epoch=result.justified_epoch,
                finalized_epoch=result.finalized_epoch,
                justified_finalized_gap=result.justified_finalized_gap,
                time_since_finality_advance=result.time_since_finality_advance,
                head_to_finalized_time=result.head_to_finalized_time,
            )
        )
        await session.commit()


@activity.defn
async def store_evm_l2_finality(result: EvmL2FinalityResult) -> None:
    """Persist a standard EVM L2 finality snapshot to the database."""
    async with _session_factory()() as session:
        session.add(
            FinalityEvmL2(
                chain=ChainType(result.chain),
                latest_height=result.latest_height,
                safe_height=result.safe_height,
                finalized_height=result.finalized_height,
                latest_to_safe_blocks=result.latest_to_safe_blocks,
                safe_to_finalized_blocks=result.safe_to_finalized_blocks,
                time_since_last_head=result.time_since_last_head,
                height_correlation=result.height_correlation,
                time_to_hard_finality=result.time_to_hard_finality,
            )
        )
        await session.commit()


@activity.defn
async def store_op_stack_finality(result: OPStackFinalityResult) -> None:
    """Persist an OP Stack finality snapshot to the database."""
    async with _session_factory()() as session:
        session.add(
            FinalityOpStack(
                chain=ChainType(result.chain),
                unsafe_height=result.unsafe_height,
                safe_height=result.safe_height,
                finalized_height=result.finalized_height,
                unsafe_to_safe_blocks=result.unsafe_to_safe_blocks,
                safe_to_finalized_blocks=result.safe_to_finalized_blocks,
                time_since_last_unsafe=result.time_since_last_unsafe,
                height_correlation=result.height_correlation,
                time_to_hard_finality=result.time_to_hard_finality,
            )
        )
        await session.commit()


@activity.defn
async def store_polygon_finality(result: PolygonFinalityResult) -> None:
    """Persist a Polygon finality snapshot to the database."""
    async with _session_factory()() as session:
        session.add(
            FinalityPolygon(
                latest_height=result.latest_height,
                finalized_height=result.finalized_height,
                latest_to_finalized_blocks=result.latest_to_finalized_blocks,
                time_since_last_head=result.time_since_last_head,
            )
        )
        await session.commit()


@activity.defn
async def store_solana_finality(result: SolanaFinalityResult) -> None:
    """Persist a Solana finality snapshot to the database."""
    async with _session_factory()() as session:
        session.add(
            FinalitySolana(
                processed_slot=result.processed_slot,
                confirmed_slot=result.confirmed_slot,
                finalized_slot=result.finalized_slot,
                confirmed_finalized_gap=result.confirmed_finalized_gap,
                processed_confirmed_gap=result.processed_confirmed_gap,
            )
        )
        await session.commit()
