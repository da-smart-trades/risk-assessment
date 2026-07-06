# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from temporalio import workflow
from temporalio.common import RetryPolicy

if TYPE_CHECKING:
    from .schemas import CantonDecentralizationResult, CantonFinalityResult

with workflow.unsafe.imports_passed_through():
    from cert_ra.metrics.canton import activities

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

# Scan calls fan out across several SV endpoints and the decentralization
# fetch paginates the validator-license list, so it gets a longer timeout.
_FINALITY_FETCH_TIMEOUT = timedelta(minutes=2)
_DECENTRALIZATION_FETCH_TIMEOUT = timedelta(minutes=5)
_STORE_TIMEOUT = timedelta(minutes=1)


@workflow.defn
class CantonFinalityWorkflow:
    """Fetch and persist a combined Canton finality snapshot."""

    @workflow.run
    async def run(self) -> None:
        """Fetch then persist a Canton finality snapshot."""
        result: CantonFinalityResult = await workflow.execute_activity(
            activities.fetch_canton_finality,
            start_to_close_timeout=_FINALITY_FETCH_TIMEOUT,
            retry_policy=_FETCH_RETRY,
        )
        await workflow.execute_activity(
            activities.store_canton_finality,
            result,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )


@workflow.defn
class CantonDecentralizationWorkflow:
    """Fetch and persist a Canton Super-Validator decentralization snapshot."""

    @workflow.run
    async def run(self) -> None:
        """Fetch then persist a Canton decentralization snapshot."""
        result: CantonDecentralizationResult = await workflow.execute_activity(
            activities.fetch_canton_decentralization,
            start_to_close_timeout=_DECENTRALIZATION_FETCH_TIMEOUT,
            retry_policy=_FETCH_RETRY,
        )
        await workflow.execute_activity(
            activities.store_canton_decentralization,
            result,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )
