# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Standalone Temporal worker for the alerts subsystem.

Run with::

    uv run python -m cert_ra.alerts.worker

or, after ``uv sync``::

    certora-risk-alerts-worker

Operates on the ``"alerts"`` task queue. Sibling to ``cert_ra.metrics.worker``
on the ``"metrics"`` queue — the two are deliberately separate processes so
that an alerting-side stall (stuck webhook, slow SMTP) cannot stall metric
ingestion (and vice versa). Both connect to the same Temporal namespace and
share the same database, but nothing else.

On first startup the worker creates two Temporal schedules:

- ``alerts-evaluator`` — every 30 s, runs ``AlertsEvaluatorWorkflow``.
- ``alerts-notification-dispatch`` — every 15 s, runs
  ``NotificationDispatchWorkflow``.

Subsequent startups skip schedule creation if the schedule already exists.

The whole worker is gated behind ``cert_ra_temporal_alerts_enabled``. When the
flag is false the worker exits with a single log line — used to land the
worker dark before flipping it on in production.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleSpec,
)
from temporalio.service import RPCError
from temporalio.worker import Worker

from cert_ra.alerts.activities import (
    claim_pending_notifications,
    dispatch_notification,
    enqueue_notifications,
    evaluate_rules,
    load_enabled_rules,
    load_historical_series,
    load_latest_snapshots,
    load_previous_status,
    mark_notification_failed,
    mark_notification_sent,
    persist_evaluation_results,
)
from cert_ra.alerts.workflows import (
    AlertsEvaluatorWorkflow,
    NotificationDispatchWorkflow,
)
from cert_ra.settings.temporal import get_temporal_settings
from cert_ra.temporal.client import connect_temporal

logger = logging.getLogger(__name__)

TASK_QUEUE = "alerts"
"""Dedicated Temporal task queue. Do not reuse the ``metrics`` queue."""

_EVALUATOR_INTERVAL = timedelta(seconds=30)
_DISPATCH_INTERVAL = timedelta(seconds=15)


_WORKFLOWS: list[Any] = [
    AlertsEvaluatorWorkflow,
    NotificationDispatchWorkflow,
]

_ACTIVITIES: list[Any] = [
    load_enabled_rules,
    load_latest_snapshots,
    load_historical_series,
    load_previous_status,
    evaluate_rules,
    persist_evaluation_results,
    enqueue_notifications,
    claim_pending_notifications,
    dispatch_notification,
    mark_notification_sent,
    mark_notification_failed,
]


_SCHEDULES: list[tuple[str, Any, timedelta]] = [
    ("alerts-evaluator", AlertsEvaluatorWorkflow, _EVALUATOR_INTERVAL),
    ("alerts-notification-dispatch", NotificationDispatchWorkflow, _DISPATCH_INTERVAL),
]


async def _ensure_schedules(client: Client) -> None:
    """Create the alerts schedules idempotently, targeting the alerts queue."""
    for schedule_id, workflow_cls, interval in _SCHEDULES:
        spec = ScheduleSpec(intervals=[ScheduleIntervalSpec(every=interval)])
        action = ScheduleActionStartWorkflow(
            workflow_cls.run,
            id=schedule_id,
            task_queue=TASK_QUEUE,
        )
        try:
            await client.create_schedule(
                schedule_id, Schedule(action=action, spec=spec)
            )
            logger.info("Created alerts schedule %s", schedule_id)
        except (RPCError, ScheduleAlreadyRunningError):
            logger.debug("Alerts schedule %s already exists, skipping", schedule_id)


async def run_worker() -> None:
    """Connect to Temporal, ensure schedules, and run the worker.

    Exits cleanly with a log message if ``cert_ra_temporal_alerts_enabled`` is
    false. Uses the same Temporal connection settings as the metrics worker.
    """
    settings = get_temporal_settings()
    if not settings.alerts_enabled:
        logger.warning(
            "Alerts worker is disabled (cert_ra_temporal_alerts_enabled=false); exiting."
        )
        return

    client = await connect_temporal(settings)

    await _ensure_schedules(client)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=_WORKFLOWS,
        activities=_ACTIVITIES,
    )

    logger.info("Alerts worker starting on task queue %s.", TASK_QUEUE)
    await worker.run()


def main() -> None:
    """Script entrypoint for the ``certora-risk-alerts-worker`` console script."""
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
