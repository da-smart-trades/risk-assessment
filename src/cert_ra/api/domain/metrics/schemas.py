# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from decimal import Decimal  # noqa: TC003
from uuid import UUID  # noqa: TC003

from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.types import ChainType, MetricType, TokenType  # noqa: TC001

__all__ = (
    "ChainList",
    "Decentralization",
    "DecentralizationCanton",
    "FinalityCanton",
    "FinalityEthereum",
    "FinalityEvmL2",
    "FinalityOpStack",
    "FinalityPolygon",
    "FinalitySolana",
    "Governance",
    "OperatorEntry",
    "OperatorSnapshot",
    "Throughput",
    "TimeToFinality",
    "TokenActivitySnapshot",
    "TokenList",
)


class ChainList(CamelizedBaseStruct):
    """Response containing all available chains."""

    chains: list[ChainType]


class TokenList(CamelizedBaseStruct):
    """Response containing all available tokens."""

    tokens: list[TokenType]


class FinalityEthereum(CamelizedBaseStruct):
    """Ethereum finality snapshot response."""

    id: UUID
    created_at: datetime
    head_height: int
    finalized_height: int
    safe_height: int
    justified_epoch: int
    finalized_epoch: int
    justified_finalized_gap: int
    time_since_finality_advance: float
    head_to_finalized_time: int


class FinalityEvmL2(CamelizedBaseStruct):
    """Standard EVM L2 finality snapshot response (Arbitrum / Base)."""

    id: UUID
    created_at: datetime
    chain: ChainType
    latest_height: int
    safe_height: int
    finalized_height: int
    latest_to_safe_blocks: int
    safe_to_finalized_blocks: int
    time_since_last_head: float
    height_correlation: int | None
    time_to_hard_finality: int | None


class FinalityOpStack(CamelizedBaseStruct):
    """OP Stack finality snapshot response (Ink / Unichain)."""

    id: UUID
    created_at: datetime
    chain: ChainType
    unsafe_height: int
    safe_height: int
    finalized_height: int
    unsafe_to_safe_blocks: int
    safe_to_finalized_blocks: int
    time_since_last_unsafe: float
    height_correlation: int
    time_to_hard_finality: int


class FinalityPolygon(CamelizedBaseStruct):
    """Polygon finality snapshot response."""

    id: UUID
    created_at: datetime
    latest_height: int
    finalized_height: int
    latest_to_finalized_blocks: int
    time_since_last_head: float


class FinalitySolana(CamelizedBaseStruct):
    """Solana finality snapshot response."""

    id: UUID
    created_at: datetime
    processed_slot: int
    confirmed_slot: int
    finalized_slot: int
    confirmed_finalized_gap: int
    processed_confirmed_gap: int


class Throughput(CamelizedBaseStruct):
    """Throughput snapshot response (gas price, TPS, BPS)."""

    id: UUID
    created_at: datetime
    chain: ChainType
    gas_price: float
    transactions_per_second: float
    blocks_per_second: float


class TimeToFinality(CamelizedBaseStruct):
    """Soft time-to-finality snapshot response."""

    id: UUID
    created_at: datetime
    chain: ChainType
    soft_finality_seconds: float


class Decentralization(CamelizedBaseStruct):
    """Decentralization snapshot response (combined validator-stake metrics)."""

    id: UUID
    created_at: datetime
    chain: ChainType
    total_amount_of_stakes: float
    number_of_nodes: int
    nakamoto_liveness_coefficient: int
    nakamoto_safety_coefficient: int
    hhi: float
    shapley_top_value: float
    shapley_second_value: float
    shapley_third_value: float
    renyi_entropy_alpha_0: float
    renyi_entropy_alpha_1: float
    renyi_entropy_alpha_2: float
    renyi_entropy_alpha_inf: float


class FinalityCanton(CamelizedBaseStruct):
    """Combined Canton finality snapshot response.

    Canton finality is deterministic, so this carries round cadence / ledger
    freshness signals alongside the SV BFT quorum margin rather than block
    heights.
    """

    id: UUID
    created_at: datetime
    latest_round_number: int
    round_advance_seconds: float
    round_window_seconds: float
    open_round_count: int
    ledger_freshness_seconds: float
    live_sv_count: int
    voting_threshold: int
    sv_quorum_margin: int


class DecentralizationCanton(CamelizedBaseStruct):
    """Canton Super-Validator governance-decentralization snapshot response."""

    id: UUID
    created_at: datetime
    sv_count: int
    validator_count: int
    voting_threshold: int
    gov_nakamoto_safety: int
    gov_nakamoto_liveness: int
    distinct_sequencer_count: int


class OperatorEntry(CamelizedBaseStruct):
    """One row in the top-operators table — a single staking entity."""

    rank: int
    operator_id: str
    name: str
    validator_count: int
    stake: float
    stake_share: float


class OperatorSnapshot(CamelizedBaseStruct):
    """Top-operators snapshot for a chain (Rated Network for Ethereum).

    ``coverage_pct`` is the fraction of the chain's total stake mapped to a
    labeled entity — the remainder is unlabeled / solo stakers.
    """

    id: UUID
    created_at: datetime
    chain: ChainType
    entity_nakamoto_liveness: int
    entity_nakamoto_safety: int
    entity_count: int
    coverage_pct: float
    top_operators: list[OperatorEntry]


class TokenActivitySnapshot(CamelizedBaseStruct):
    """Token activity snapshot response (inflow, outflow, supply, etc.)."""

    id: UUID
    created_at: datetime
    chain: ChainType
    token: TokenType
    metric_type: MetricType
    value: Decimal


class Governance(CamelizedBaseStruct):
    """Governance event snapshot.

    One row per ``(chain, event_type)`` poll. ``event_type`` is one of
    ``"proposals"``, ``"execution"``, or ``"emergency"`` and ``count`` is the
    number of events observed in that poll's window.
    """

    id: UUID
    created_at: datetime
    chain: ChainType
    event_type: str
    count: int
