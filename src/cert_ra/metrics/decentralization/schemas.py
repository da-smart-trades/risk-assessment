# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

# ``(validator_id, stake)`` tuples in native units (ETH, SOL, POL, AVAX).
ValidatorStakes = list[tuple[str, float]]


# Chains that have a per-validator stake source wired up.
SUPPORTED_CHAINS: tuple[str, ...] = (
    "ETHEREUM",
    "SOLANA",
    "POLYGON",
    "AVALANCHE_C",
)


class DecentralizationResult(BaseModel):
    """All decentralization metrics computed from the same validator stake sample."""

    chain: str
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


class DecentralizationParams(BaseModel):
    """Single-chain decentralization workflow input."""

    chain: str


# Chains that have an operator/entity label source wired up. Each chain has
# its own data source (see ``activities._OPERATOR_FETCHERS``) — Ethereum via
# Rated Network, Polygon via its staking API, Avalanche via P-Chain RPC +
# curated labels, Solana via getVoteAccounts + curated labels.
OPERATOR_SUPPORTED_CHAINS: tuple[str, ...] = (
    "ETHEREUM",
    "SOLANA",
    "POLYGON",
    "AVALANCHE_C",
)


class OperatorEntry(BaseModel):
    """One row in the top-operators table persisted with each snapshot.

    Fields use snake_case in Python but are serialized to JSON / JSONB with
    camelCase keys (``operatorId``, ``validatorCount``, ``stakeShare``) so the
    persisted shape matches the API contract consumed by the React frontend.
    Always dump with ``model_dump(by_alias=True)`` when writing to the DB.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    rank: int
    operator_id: str
    name: str
    validator_count: int
    stake: float
    stake_share: float


class OperatorSnapshotResult(BaseModel):
    """Output of ``fetch_operator_snapshot`` — persisted as one DB row."""

    chain: str
    entity_nakamoto_liveness: int
    entity_nakamoto_safety: int
    entity_count: int
    coverage_pct: float
    top_operators: list[OperatorEntry]


class OperatorSnapshotParams(BaseModel):
    """Single-chain operator refresh workflow input."""

    chain: str
