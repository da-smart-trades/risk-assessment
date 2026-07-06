# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from temporalio import workflow
from temporalio.common import RetryPolicy

# ChainParams must be importable at runtime: Temporal resolves parameter types
# via get_type_hints() against the module's global namespace.
from .schemas import ChainParams  # noqa: TC001

if TYPE_CHECKING:
    from .schemas import (
        EthFinalityResult,
        EvmL2FinalityResult,
        OPStackFinalityResult,
        PolygonFinalityResult,
        SolanaFinalityResult,
    )

with workflow.unsafe.imports_passed_through():
    from cert_ra.metrics.finality import activities

# ---------------------------------------------------------------------------
# Shared retry policies
# ---------------------------------------------------------------------------

_CHAIN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=10,
    non_retryable_error_types=["ValidationError"],
)

_STORE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
)

_FETCH_TIMEOUT = timedelta(minutes=2)
_STORE_TIMEOUT = timedelta(minutes=1)

# ---------------------------------------------------------------------------
# Per-chain workflows — each runs on its own 30-second Temporal schedule
# ---------------------------------------------------------------------------


@workflow.defn
class EthereumFinalityWorkflow:
    """Fetch and persist an Ethereum finality snapshot."""

    @workflow.run
    async def run(self) -> None:
        """Fetch then persist an Ethereum finality snapshot."""
        result: EthFinalityResult = await workflow.execute_activity(
            activities.fetch_ethereum_finality,
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_CHAIN_RETRY,
        )
        await workflow.execute_activity(
            activities.store_ethereum_finality,
            result,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )


@workflow.defn
class EvmL2FinalityWorkflow:
    """Fetch and persist a standard EVM L2 finality snapshot (Arbitrum, Base, or Optimism)."""

    @workflow.run
    async def run(self, params: ChainParams) -> None:
        """Fetch then persist an EVM L2 finality snapshot for ``params.chain``."""
        result: EvmL2FinalityResult = await workflow.execute_activity(
            activities.fetch_evm_l2_finality,
            params.chain,
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_CHAIN_RETRY,
        )
        await workflow.execute_activity(
            activities.store_evm_l2_finality,
            result,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )


@workflow.defn
class OPStackFinalityWorkflow:
    """Fetch and persist an OP Stack finality snapshot (Ink or Unichain)."""

    @workflow.run
    async def run(self, params: ChainParams) -> None:
        """Fetch then persist an OP Stack finality snapshot for ``params.chain``."""
        result: OPStackFinalityResult = await workflow.execute_activity(
            activities.fetch_op_stack_finality,
            params.chain,
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_CHAIN_RETRY,
        )
        await workflow.execute_activity(
            activities.store_op_stack_finality,
            result,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )


@workflow.defn
class PolygonFinalityWorkflow:
    """Fetch and persist a Polygon finality snapshot."""

    @workflow.run
    async def run(self) -> None:
        """Fetch then persist a Polygon finality snapshot."""
        result: PolygonFinalityResult = await workflow.execute_activity(
            activities.fetch_polygon_finality,
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_CHAIN_RETRY,
        )
        await workflow.execute_activity(
            activities.store_polygon_finality,
            result,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )


@workflow.defn
class SolanaFinalityWorkflow:
    """Fetch and persist a Solana finality snapshot."""

    @workflow.run
    async def run(self) -> None:
        """Fetch then persist a Solana finality snapshot."""
        result: SolanaFinalityResult = await workflow.execute_activity(
            activities.fetch_solana_finality,
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_CHAIN_RETRY,
        )
        await workflow.execute_activity(
            activities.store_solana_finality,
            result,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )
