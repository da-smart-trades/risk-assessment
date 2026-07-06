# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from temporalio import workflow
from temporalio.common import RetryPolicy

from .schemas import TokenActivityParams  # noqa: TC001

if TYPE_CHECKING:
    from .schemas import TokenActivityBatchResult

with workflow.unsafe.imports_passed_through():
    from cert_ra.metrics.tokens import activities

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
class TokenActivityWorkflow:
    """Fetch and persist all token-activity metrics for a ``(chain, token)`` pair."""

    @workflow.run
    async def run(self, params: TokenActivityParams) -> None:
        """Refresh all metrics for ``(params.chain, params.token)``."""
        batch: TokenActivityBatchResult = await workflow.execute_activity(
            activities.fetch_token_activity,
            params,
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_FETCH_RETRY,
        )
        await workflow.execute_activity(
            activities.store_token_activity,
            batch,
            start_to_close_timeout=_STORE_TIMEOUT,
            retry_policy=_STORE_RETRY,
        )
