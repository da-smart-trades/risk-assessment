# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Workflows for the alerts subsystem.

Two scheduled workflows on the ``"alerts"`` task queue:

- ``AlertsEvaluatorWorkflow`` — runs every 30 s. Loads enabled rules, fetches
  the latest snapshots + any historical series the rules need, runs the pure
  evaluator, persists state-change events, and enqueues notifications.
- ``NotificationDispatchWorkflow`` — runs every 15 s. Drains the
  ``notification`` queue, dispatching each row through its integration
  channel.

Both workflows keep all I/O inside activities so the workflow body stays
deterministic and replay-safe.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from temporalio import workflow
from temporalio.common import RetryPolicy

if TYPE_CHECKING:
    from cert_ra.alerts.schemas import EvaluationEvent

with workflow.unsafe.imports_passed_through():
    from cert_ra.alerts import activities
    from cert_ra.alerts.schemas import EvaluatorInput

__all__ = ("AlertsEvaluatorWorkflow", "NotificationDispatchWorkflow")


_DB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
)
"""Retry policy for fast internal DB writes / reads."""

_DISPATCH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=3,
    non_retryable_error_types=["ValidationError"],
)

_LOAD_TIMEOUT = timedelta(seconds=30)
_HISTORY_TIMEOUT = timedelta(seconds=60)
"""Historical reads scan a range rather than one row — give them more headroom."""
_EVALUATE_TIMEOUT = timedelta(seconds=15)
_PERSIST_TIMEOUT = timedelta(seconds=30)
_DISPATCH_TIMEOUT = timedelta(seconds=20)


@workflow.defn
class AlertsEvaluatorWorkflow:
    """Periodic evaluator. Runs the full pipeline once per tick."""

    @workflow.run
    async def run(self) -> None:
        """Execute one evaluator pass.

        Activities are awaited sequentially because each step's output feeds
        the next. Fan-out is not needed at MVP scale — load-rules is one
        query, evaluate is pure Python, persist is a single transaction.
        """
        rules = await workflow.execute_activity(
            activities.load_enabled_rules,
            start_to_close_timeout=_LOAD_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        if not rules:
            return

        snapshots_by_rule = await workflow.execute_activity(
            activities.load_latest_snapshots,
            rules,
            start_to_close_timeout=_LOAD_TIMEOUT,
            retry_policy=_DB_RETRY,
        )

        historical_series_by_rule = await workflow.execute_activity(
            activities.load_historical_series,
            rules,
            start_to_close_timeout=_HISTORY_TIMEOUT,
            retry_policy=_DB_RETRY,
        )

        previous_status = await workflow.execute_activity(
            activities.load_previous_status,
            [(rule.alert_id, rule.team_id) for rule in rules],
            start_to_close_timeout=_LOAD_TIMEOUT,
            retry_policy=_DB_RETRY,
        )

        events: list[EvaluationEvent] = await workflow.execute_activity(
            activities.evaluate_rules,
            EvaluatorInput(
                rules=rules,
                snapshots_by_rule=snapshots_by_rule,
                historical_series_by_rule=historical_series_by_rule,
                previous_status=previous_status,
            ),
            start_to_close_timeout=_EVALUATE_TIMEOUT,
        )
        if not events:
            return

        history_ids = await workflow.execute_activity(
            activities.persist_evaluation_results,
            events,
            start_to_close_timeout=_PERSIST_TIMEOUT,
            retry_policy=_DB_RETRY,
        )

        await workflow.execute_activity(
            activities.enqueue_notifications,
            list(zip(events, history_ids, strict=True)),
            start_to_close_timeout=_PERSIST_TIMEOUT,
            retry_policy=_DB_RETRY,
        )


@workflow.defn
class NotificationDispatchWorkflow:
    """Periodic dispatcher. Drains the notification queue."""

    @workflow.run
    async def run(self) -> None:
        """Claim a batch of pending rows, dispatch each one, record the outcome."""
        notification_ids = await workflow.execute_activity(
            activities.claim_pending_notifications,
            start_to_close_timeout=_LOAD_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        for notification_id in notification_ids:
            outcome = await workflow.execute_activity(
                activities.dispatch_notification,
                notification_id,
                start_to_close_timeout=_DISPATCH_TIMEOUT,
                retry_policy=_DISPATCH_RETRY,
            )
            if outcome.success:
                await workflow.execute_activity(
                    activities.mark_notification_sent,
                    notification_id,
                    start_to_close_timeout=_PERSIST_TIMEOUT,
                    retry_policy=_DB_RETRY,
                )
            else:
                await workflow.execute_activity(
                    activities.mark_notification_failed,
                    args=[notification_id, outcome.error or "Unknown error"],
                    start_to_close_timeout=_PERSIST_TIMEOUT,
                    retry_policy=_DB_RETRY,
                )
