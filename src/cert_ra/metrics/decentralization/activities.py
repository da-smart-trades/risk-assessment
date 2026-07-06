# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from temporalio import activity

from cert_ra.db.models import Decentralization, DecentralizationOperatorSnapshot
from cert_ra.metrics._session import session_factory
from cert_ra.types import ChainType

from .avalanche_operators import fetch_avalanche_operators
from .calculations import (
    LIVENESS_THRESHOLD,
    SAFETY_THRESHOLD,
    hhi,
    nakamoto_coefficient,
    renyi_entropy,
    shapley_top_values,
)
from .polygon_operators import fetch_polygon_operators
from .rated import RatedOperator, fetch_ethereum_operators
from .schemas import (
    OPERATOR_SUPPORTED_CHAINS,
    SUPPORTED_CHAINS,
    DecentralizationResult,
    OperatorEntry,
    OperatorSnapshotResult,
)
from .solana_operators import fetch_solana_operators
from .validators import (
    fetch_avalanche_stakes,
    fetch_ethereum_stakes,
    fetch_polygon_stakes,
    fetch_solana_stakes,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .schemas import ValidatorStakes

_STAKE_FETCHERS: dict[ChainType, Callable[[], Awaitable[ValidatorStakes]]] = {
    ChainType.ETHEREUM: fetch_ethereum_stakes,
    ChainType.SOLANA: fetch_solana_stakes,
    ChainType.POLYGON: fetch_polygon_stakes,
    ChainType.AVALANCHE_C: fetch_avalanche_stakes,
}

_OPERATOR_FETCHERS: dict[ChainType, Callable[[], Awaitable[list[RatedOperator]]]] = {
    ChainType.ETHEREUM: fetch_ethereum_operators,
    ChainType.SOLANA: fetch_solana_operators,
    ChainType.POLYGON: fetch_polygon_operators,
    ChainType.AVALANCHE_C: fetch_avalanche_operators,
}

_TOP_OPERATOR_COUNT = 10


def _compute(chain: str, stakes: ValidatorStakes) -> DecentralizationResult:
    positive = [s for _, s in stakes if s > 0]
    if not positive:
        msg = f"decentralization: no positive-stake validators for {chain}"
        raise RuntimeError(msg)

    shapley = shapley_top_values(positive)
    # Always expose three Shapley slots even if the network has < 3 validators.
    top = shapley[0] if len(shapley) > 0 else 0.0
    second = shapley[1] if len(shapley) > 1 else 0.0
    third = shapley[2] if len(shapley) > 2 else 0.0  # noqa: PLR2004

    return DecentralizationResult(
        chain=chain,
        total_amount_of_stakes=sum(positive),
        number_of_nodes=len(positive),
        nakamoto_liveness_coefficient=nakamoto_coefficient(
            positive, threshold=LIVENESS_THRESHOLD
        ),
        nakamoto_safety_coefficient=nakamoto_coefficient(
            positive, threshold=SAFETY_THRESHOLD
        ),
        hhi=hhi(positive),
        shapley_top_value=top,
        shapley_second_value=second,
        shapley_third_value=third,
        renyi_entropy_alpha_0=renyi_entropy(positive, alpha=0),
        renyi_entropy_alpha_1=renyi_entropy(positive, alpha=1),
        renyi_entropy_alpha_2=renyi_entropy(positive, alpha=2),
        renyi_entropy_alpha_inf=renyi_entropy(positive, alpha=math.inf),
    )


@activity.defn
async def fetch_decentralization(chain: str) -> DecentralizationResult:
    """Fetch validator stakes for a chain and compute all decentralization metrics."""
    chain_upper = chain.upper()
    if chain_upper not in SUPPORTED_CHAINS:
        msg = f"decentralization: chain {chain_upper} not supported"
        raise ValueError(msg)

    fetcher = _STAKE_FETCHERS[ChainType(chain_upper)]
    stakes = await fetcher()
    return _compute(chain_upper, stakes)


@activity.defn
async def store_decentralization(result: DecentralizationResult) -> None:
    """Persist a decentralization snapshot to the database."""
    async with session_factory()() as session:
        session.add(
            Decentralization(
                chain=ChainType(result.chain),
                total_amount_of_stakes=result.total_amount_of_stakes,
                number_of_nodes=result.number_of_nodes,
                nakamoto_liveness_coefficient=result.nakamoto_liveness_coefficient,
                nakamoto_safety_coefficient=result.nakamoto_safety_coefficient,
                hhi=result.hhi,
                shapley_top_value=result.shapley_top_value,
                shapley_second_value=result.shapley_second_value,
                shapley_third_value=result.shapley_third_value,
                renyi_entropy_alpha_0=result.renyi_entropy_alpha_0,
                renyi_entropy_alpha_1=result.renyi_entropy_alpha_1,
                renyi_entropy_alpha_2=result.renyi_entropy_alpha_2,
                renyi_entropy_alpha_inf=result.renyi_entropy_alpha_inf,
            )
        )
        await session.commit()


async def _ethereum_coverage(operator_total: float) -> float:
    """Cross-check Rated's total against the Beacon API — anything not in
    Rated's dataset is unlabeled stake.
    """
    beacon_stakes = await fetch_ethereum_stakes()
    beacon_total = sum(s for _, s in beacon_stakes if s > 0)
    if beacon_total <= 0:
        return 0.0
    return min(operator_total / beacon_total, 1.0)


@activity.defn
async def fetch_operator_snapshot(chain: str) -> OperatorSnapshotResult:
    """Pull per-chain operator aggregates and compute entity Nakamoto.

    For Ethereum, coverage cross-checks Rated's total against the Beacon API.
    For the other chains coverage is the fraction of operator stake whose
    name came from an authoritative label (the staking API or the curated
    labels file) — the remainder is anonymous solo-looking validators.
    """
    chain_upper = chain.upper()
    if chain_upper not in OPERATOR_SUPPORTED_CHAINS:
        msg = f"operator snapshot: chain {chain_upper} not supported"
        raise ValueError(msg)

    fetcher = _OPERATOR_FETCHERS[ChainType(chain_upper)]
    operators = await fetcher()
    if not operators:
        msg = f"operator snapshot: no operators returned for {chain_upper}"
        raise RuntimeError(msg)

    operator_total = sum(op.total_effective_balance_eth for op in operators)
    operator_stakes = [
        op.total_effective_balance_eth
        for op in operators
        if op.total_effective_balance_eth > 0
    ]
    if not operator_stakes:
        msg = f"operator snapshot: every {chain_upper} operator has zero stake"
        raise RuntimeError(msg)

    if chain_upper == "ETHEREUM":
        coverage = await _ethereum_coverage(operator_total)
    else:
        labeled_total = sum(
            op.total_effective_balance_eth for op in operators if op.labeled
        )
        coverage = labeled_total / operator_total if operator_total > 0 else 0.0

    nakamoto_liveness = nakamoto_coefficient(
        operator_stakes, threshold=LIVENESS_THRESHOLD
    )
    nakamoto_safety = nakamoto_coefficient(operator_stakes, threshold=SAFETY_THRESHOLD)

    sorted_ops = sorted(
        operators, key=lambda op: op.total_effective_balance_eth, reverse=True
    )
    top = sorted_ops[:_TOP_OPERATOR_COUNT]
    top_entries = [
        OperatorEntry(
            rank=i + 1,
            operator_id=op.operator_id,
            name=op.name,
            validator_count=op.validator_count,
            stake=op.total_effective_balance_eth,
            stake_share=(
                op.total_effective_balance_eth / operator_total
                if operator_total > 0
                else 0.0
            ),
        )
        for i, op in enumerate(top)
    ]

    return OperatorSnapshotResult(
        chain=chain_upper,
        entity_nakamoto_liveness=nakamoto_liveness,
        entity_nakamoto_safety=nakamoto_safety,
        entity_count=len(operator_stakes),
        coverage_pct=coverage,
        top_operators=top_entries,
    )


@activity.defn
async def store_operator_snapshot(result: OperatorSnapshotResult) -> None:
    """Persist a top-operators snapshot for the chain."""
    async with session_factory()() as session:
        session.add(
            DecentralizationOperatorSnapshot(
                chain=ChainType(result.chain),
                entity_nakamoto_liveness=result.entity_nakamoto_liveness,
                entity_nakamoto_safety=result.entity_nakamoto_safety,
                entity_count=result.entity_count,
                coverage_pct=result.coverage_pct,
                top_operators=[
                    op.model_dump(by_alias=True) for op in result.top_operators
                ],
            )
        )
        await session.commit()
