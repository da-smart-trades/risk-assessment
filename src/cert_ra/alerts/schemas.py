# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Workflow / activity input + output schemas.

Plain ``@dataclass`` per the repo's Temporal convention (Pydantic data converter
serialises them; switching to Pydantic models is also fine but adds no value
for the simple shapes we have here).

Snapshots and historical series are keyed by ``rule_id`` at the workflow level
rather than by a metric tuple, because target shapes after the
``target_config`` refactor are heterogeneous (anchor sub_category, market id,
…) and a single composite key no longer captures them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003 — runtime type for Pydantic data converter
from typing import Any
from uuid import UUID  # noqa: TC003 — runtime type for Pydantic data converter

__all__ = (
    "DispatchOutcome",
    "EvaluationEvent",
    "EvaluatorInput",
    "HistoricalSeries",
    "MetricSnapshot",
    "RuleSummary",
)


@dataclass
class RuleSummary:
    """Flattened view of an enabled alert rule with its effective integrations.

    Built by ``load_enabled_rules``; consumed by ``evaluate_rules`` and
    ``enqueue_notifications``. Carries everything the pure evaluator needs
    without keeping the SQLAlchemy session open across activities. The
    ``target_kind`` + ``target_config`` pair replaces the previous
    ``(metric_type, chain, token)`` triple.
    """

    alert_id: UUID
    team_id: UUID
    is_template: bool
    name: str
    severity: str
    target_kind: str
    target_config: dict[str, Any]
    rule_kind: str
    rule_config: dict[str, Any]
    integration_ids: list[UUID]


@dataclass
class MetricSnapshot:
    """The latest observed value for one rule's target.

    Identity lives on the parent rule (matched by ``rule_id`` in the maps the
    workflow passes around) — the snapshot itself just carries the numeric
    observation plus the provenance recorded onto ``alert_history.context``
    for post-incident analysis.
    """

    value: float
    observed_at: datetime
    snapshot_id: UUID | None
    snapshot_table: str


@dataclass
class HistoricalSeries:
    """A range of observations for one rule's target, oldest → newest.

    Empty when the lookback window contains no rows (or no usable rows — e.g.
    a market snapshot exists but the targeted sub_category was absent from the
    scorer JSONB). The evaluator decides what to do with an empty series per
    rule kind: rate-of-change without history raises ERROR; stddev with fewer
    than ``MIN_STDDEV_SAMPLES`` raises ERROR.
    """

    samples: list[tuple[datetime, float]] = field(default_factory=list)


@dataclass
class EvaluatorInput:
    """Bundle of inputs handed to ``evaluate_rules``.

    The two maps are keyed by ``rule_id`` rather than by any target-shaped
    composite, so adding new ``AlertTargetKind`` variants does not require a
    new key shape.
    """

    rules: list[RuleSummary]
    snapshots_by_rule: dict[UUID, MetricSnapshot]
    historical_series_by_rule: dict[UUID, HistoricalSeries]
    previous_status: dict[str, str]
    """Map of ``"{alert_id}:{team_id}"`` → previous status (``OK`` / ``TRIGGERED``).

    Computed by ``load_previous_status`` from the latest ``alert_history`` row
    per (alert, team). Missing entries are treated as ``OK``.
    """


@dataclass
class EvaluationEvent:
    """One evaluator-tick result that needs to be persisted."""

    alert_id: UUID
    team_id: UUID
    status: str  # OK / TRIGGERED / RECOVERED / ERROR
    metric_value: float | None
    threshold: float | None
    message: str | None
    context: dict[str, Any]
    evaluated_at: datetime
    integration_ids: list[UUID]


@dataclass
class DispatchOutcome:
    """Result of attempting one notification delivery."""

    notification_id: UUID
    success: bool
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
