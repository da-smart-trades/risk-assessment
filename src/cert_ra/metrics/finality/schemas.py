# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from pydantic import BaseModel


class EthFinalityResult(BaseModel):
    """Ethereum finality snapshot from the execution layer and beacon chain."""

    head_height: int
    finalized_height: int
    safe_height: int
    justified_epoch: int
    finalized_epoch: int
    justified_finalized_gap: int
    time_since_finality_advance: float
    head_to_finalized_time: int


class EvmL2FinalityResult(BaseModel):
    """Standard EVM L2 finality snapshot (latest → safe → finalized).

    Used for Arbitrum, Base, and Optimism. ``height_correlation`` and
    ``time_to_hard_finality`` are ``None`` for Base, which does not expose
    equivalent safe/finalized timing data.
    """

    chain: str
    latest_height: int
    safe_height: int
    finalized_height: int
    latest_to_safe_blocks: int
    safe_to_finalized_blocks: int
    time_since_last_head: float
    height_correlation: int | None = None
    time_to_hard_finality: int | None = None


class OPStackFinalityResult(BaseModel):
    """OP Stack finality snapshot (unsafe → safe → finalized).

    Used for Ink and Unichain, sourced from ``optimism_syncStatus``.
    """

    chain: str
    unsafe_height: int
    safe_height: int
    finalized_height: int
    unsafe_to_safe_blocks: int
    safe_to_finalized_blocks: int
    time_since_last_unsafe: float
    height_correlation: int
    time_to_hard_finality: int


class PolygonFinalityResult(BaseModel):
    """Polygon finality snapshot (latest → finalized, no safe stage)."""

    latest_height: int
    finalized_height: int
    latest_to_finalized_blocks: int
    time_since_last_head: float


class SolanaFinalityResult(BaseModel):
    """Solana slot commitment pipeline snapshot (processed → confirmed → finalized)."""

    processed_slot: int
    confirmed_slot: int
    finalized_slot: int
    confirmed_finalized_gap: int
    processed_confirmed_gap: int


class ChainParams(BaseModel):
    """Single-chain workflow input parameter."""

    chain: str
