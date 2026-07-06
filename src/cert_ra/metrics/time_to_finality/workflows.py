# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from temporalio import workflow
from temporalio.common import RetryPolicy

from .schemas import TimeToFinalityParams  # noqa: TC001

if TYPE_CHECKING:
    from .schemas import TimeToFinalityResult

with workflow.unsafe.imports_passed_through():
    from cert_ra.metrics.time_to_finality import activities

_FETCH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=6,
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


@workflow.defn
class TimeToFinalityWorkflow:
    """Fetch and persist a soft-finality snapshot for a chain."""

    @workflow.run
    async def run(self, params: TimeToFinalityParams) -> None:
        """Fetch then persist a soft-finality snapshot for ``params.chain``."""
        result: TimeToFinalityResult = await workflow.execute_activity(
            activities.fetch_time_to_finality,
            params.chain,
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_FETCH_RETRY,
        )
        await workflow.execute_activity(
            activities.store_time_to_finality,
            result,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )
