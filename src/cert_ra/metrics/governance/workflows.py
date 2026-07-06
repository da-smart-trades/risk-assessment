# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from temporalio import workflow
from temporalio.common import RetryPolicy

from .schemas import GovernanceParams  # noqa: TC001

if TYPE_CHECKING:
    from .schemas import GovernanceResult

with workflow.unsafe.imports_passed_through():
    from cert_ra.metrics.governance import activities

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


@workflow.defn
class GovernanceWorkflow:
    """Fetch and persist a governance event count for a ``(chain, event_type)`` pair."""

    @workflow.run
    async def run(self, params: GovernanceParams) -> None:
        """Fetch then persist a governance snapshot for ``params``."""
        result: GovernanceResult = await workflow.execute_activity(
            activities.fetch_governance,
            params,
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_FETCH_RETRY,
        )
        await workflow.execute_activity(
            activities.store_governance,
            result,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )
