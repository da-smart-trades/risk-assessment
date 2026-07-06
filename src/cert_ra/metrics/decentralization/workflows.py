# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from temporalio import workflow
from temporalio.common import RetryPolicy

from .schemas import (  # noqa: TC001
    DecentralizationParams,
    OperatorSnapshotParams,
)

if TYPE_CHECKING:
    from .schemas import DecentralizationResult, OperatorSnapshotResult

with workflow.unsafe.imports_passed_through():
    from cert_ra.metrics.decentralization import activities

_FETCH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=6,
    non_retryable_error_types=["ValidationError"],
)

_STORE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
)

_FETCH_TIMEOUT = timedelta(minutes=5)
_STORE_TIMEOUT = timedelta(minutes=1)

# Operator refresh hits Rated + the Beacon API for the full validator set, so
# it gets a longer fetch timeout than the per-chain stake sample.
_OPERATOR_FETCH_TIMEOUT = timedelta(minutes=10)


@workflow.defn
class DecentralizationWorkflow:
    """Fetch validator stakes and persist all decentralization metrics for a chain."""

    @workflow.run
    async def run(self, params: DecentralizationParams) -> None:
        """Fetch then persist a decentralization snapshot for ``params.chain``."""
        result: DecentralizationResult = await workflow.execute_activity(
            activities.fetch_decentralization,
            params.chain,
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_FETCH_RETRY,
        )
        await workflow.execute_activity(
            activities.store_decentralization,
            result,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )


@workflow.defn
class OperatorSnapshotWorkflow:
    """Refresh the top-operators view for a chain (Rated Network)."""

    @workflow.run
    async def run(self, params: OperatorSnapshotParams) -> None:
        """Pull operator labels from Rated and persist one snapshot row."""
        result: OperatorSnapshotResult = await workflow.execute_activity(
            activities.fetch_operator_snapshot,
            params.chain,
            start_to_close_timeout=_OPERATOR_FETCH_TIMEOUT,
            retry_policy=_FETCH_RETRY,
        )
        await workflow.execute_activity(
            activities.store_operator_snapshot,
            result,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )
