# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Activities for the alerts evaluator + dispatcher workflows.

Activities follow the existing repo convention: each function has a single
responsibility, opens its own SQLAlchemy session via ``_session_factory``, and
takes / returns plain dataclass-shaped values that Temporal serialises with the
Pydantic data converter.

The evaluator activities form a linear pipeline:

    load_enabled_rules
      → load_latest_snapshots
      → load_historical_series   (for RoC + stddev rules)
      → load_previous_status
      → evaluate_rules
      → persist_evaluation_results
      → enqueue_notifications

Edge-trigger semantics live inside ``evaluate_rules``: only ``OK → TRIGGERED``
and ``TRIGGERED → RECOVERED`` transitions emit events. Both ``load_latest_*``
and ``load_historical_series`` are target-agnostic — they dispatch through the
:mod:`cert_ra.alerts._value_sources` registry, so the body never needs to grow
when a new ``AlertTargetKind`` lands.

The dispatcher activities (``claim_pending_notifications`` →
``dispatch_notification`` → ``mark_notification_sent`` /
``mark_notification_failed``) drain the ``notification`` queue. Failed
deliveries roll back to ``RETRYING`` for up to ``MAX_NOTIFICATION_ATTEMPTS``
before being marked ``FAILED``.
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: TC003 — runtime type for Pydantic data converter

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity

from cert_ra.alerts._value_sources import lookup_value_source
from cert_ra.alerts._webhook import deliver_webhook
from cert_ra.alerts.schemas import (
    DispatchOutcome,
    EvaluationEvent,
    EvaluatorInput,
    HistoricalSeries,
    MetricSnapshot,
    RuleSummary,
)
from cert_ra.api.domain.alerts._encryption import decrypt_secret, is_encrypted
from cert_ra.api.domain.alerts.integrations import (
    EmailIntegrationConfig,
    WebhookIntegrationConfig,
    parse_integration_config,
)
from cert_ra.api.domain.alerts.rules import (
    RateOfChangeRuleConfig,
    StddevDeviationRuleConfig,
    ThresholdRuleConfig,
    parse_rule_config,
)
from cert_ra.api.domain.alerts.targets import (
    MetricTargetConfig,
    parse_target_config,
)
from cert_ra.api.domain.web.email import EmailMessageService
from cert_ra.api.lib.email import get_email_config
from cert_ra.db.engine_factory import create_sqlalchemy_engine
from cert_ra.db.models import (
    Alert,
    AlertHistory,
    AlertIntegration,
    Notification,
    TeamAlertOverride,
    alert_integration_link,
)
from cert_ra.types import (
    AlertHistoryStatus,
    AlertIntegrationKind,
    AlertRuleKind,
    AlertTargetKind,
    NotificationStatus,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from cert_ra.api.domain.alerts.targets import TargetConfig

logger = logging.getLogger(__name__)

MAX_NOTIFICATION_ATTEMPTS = 5
"""Cap on per-notification retry attempts before going to ``FAILED``."""

MAX_SNAPSHOT_AGE = timedelta(minutes=10)
"""Snapshots older than this produce ``ERROR`` rows instead of evaluations."""

MIN_STDDEV_SAMPLES = 10
"""Minimum samples required to compute a meaningful stddev / mean.

Rules whose lookback window contains fewer samples emit an ``ERROR`` history
row so the team sees the gap rather than silently treating the rule as not
triggered.
"""

DEFAULT_DISPATCH_BATCH = 50
"""Maximum notifications a single dispatcher tick will claim."""


@cache
def _session_factory() -> async_sessionmaker[AsyncSession]:
    """Return a process-local async session factory."""
    return async_sessionmaker(create_sqlalchemy_engine(), expire_on_commit=False)


# ---------------------------------------------------------------------------
# Evaluator activities
# ---------------------------------------------------------------------------


@activity.defn
async def load_enabled_rules() -> list[RuleSummary]:
    """Load every effective rule (alert x team) that is currently enabled.

    Joins ``alert`` with ``team_alert_override`` so a template's effective
    state per team reflects any per-team toggle. Team-defined alerts skip the
    override join. Also fetches each rule's effective integration set
    (primary integration for the team's first kind + the per-alert link
    table).

    Returns:
        Flat list of rule summaries (one per ``(alert, team)`` pair) ready for
        the pure evaluator. Empty list if no rules are enabled.
    """
    async with _session_factory()() as session:
        # Team alerts that are themselves enabled — straightforward.
        team_rule_rows = (
            (
                await session.execute(
                    select(Alert).where(
                        Alert.is_template.is_(False),
                        Alert.is_enabled.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )

        # Templates with an opted-in (or default-on) override per team.
        templates = (
            (
                await session.execute(
                    select(Alert).where(
                        Alert.is_template.is_(True),
                        Alert.is_enabled.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        overrides = (await session.execute(select(TeamAlertOverride))).scalars().all()
        overrides_by_alert: dict[UUID, list[TeamAlertOverride]] = {}
        for override_row in overrides:
            overrides_by_alert.setdefault(override_row.alert_id, []).append(
                override_row
            )

        results: list[RuleSummary] = []

        for alert in team_rule_rows:
            assert alert.team_id is not None
            integrations = await _integration_ids_for_alert(
                session, alert.id, team_id=alert.team_id, override=None
            )
            results.append(_rule_summary(alert, alert.team_id, integrations))

        # Templates: emit one RuleSummary per (template, team) where the team
        # has the template enabled. The universe of teams is derived from the
        # override table plus every team that has at least one alert_integration.
        teams_seen = {o.team_id for o in overrides}
        if teams_seen or templates:
            integration_team_rows = (
                await session.execute(select(AlertIntegration.team_id).distinct())
            ).all()
            for (team_id,) in integration_team_rows:
                teams_seen.add(team_id)
        for template in templates:
            for team_id in teams_seen:
                team_overrides = overrides_by_alert.get(template.id, [])
                override: TeamAlertOverride | None = next(
                    (o for o in team_overrides if o.team_id == team_id), None
                )
                if override is not None and not override.is_enabled:
                    continue
                integrations = await _integration_ids_for_alert(
                    session,
                    template.id,
                    team_id=team_id,
                    override=override,
                )
                results.append(_rule_summary(template, team_id, integrations))

        activity.logger.info("Loaded %d enabled rules.", len(results))
        return results


def _rule_summary(
    alert: Alert, team_id: UUID, integration_ids: list[UUID]
) -> RuleSummary:
    """Convert an ``Alert`` row + the team it applies to into the activity-safe summary."""
    return RuleSummary(
        alert_id=alert.id,
        team_id=team_id,
        is_template=alert.is_template,
        name=alert.name,
        severity=alert.severity.value,
        target_kind=alert.target_kind.value,
        target_config=dict(alert.target_config),
        rule_kind=alert.rule_kind.value,
        rule_config=dict(alert.rule_config),
        integration_ids=integration_ids,
    )


async def _integration_ids_for_alert(
    session: AsyncSession,
    alert_id: UUID,
    *,
    team_id: UUID,
    override: TeamAlertOverride | None,
) -> list[UUID]:
    """Compute the effective integration set for one (alert, team) pair.

    Resolution order:

    1. Per-team override row's ``integration_id``, if set.
    2. Per-alert ``alert_integration_link`` rows (additional integrations).
    3. Team's primary integration (any active integration with ``is_primary=True``).
    """
    integration_ids: list[UUID] = []

    if override is not None and override.integration_id is not None:
        integration_ids.append(override.integration_id)

    extra_rows = (
        await session.execute(
            select(alert_integration_link.c.integration_id).where(
                alert_integration_link.c.alert_id == alert_id
            )
        )
    ).all()
    integration_ids.extend(row[0] for row in extra_rows)

    primary_rows = (
        await session.execute(
            select(AlertIntegration.id).where(
                AlertIntegration.team_id == team_id,
                AlertIntegration.is_primary.is_(True),
                AlertIntegration.is_active.is_(True),
            )
        )
    ).all()
    integration_ids.extend(row[0] for row in primary_rows)

    seen: set[UUID] = set()
    deduped: list[UUID] = []
    for integration_id in integration_ids:
        if integration_id in seen:
            continue
        seen.add(integration_id)
        deduped.append(integration_id)
    return deduped


# ---------------------------------------------------------------------------
# Target-aware loaders (latest + historical)
# ---------------------------------------------------------------------------


def _target_dedupe_key(target_kind: str, target_config: dict[str, Any]) -> str:
    """Build a stable string key for de-duplicating identical targets.

    Two rules with the same target read the same value, so we should only
    query for it once per tick. ``msgspec`` round-trip would be cleaner but
    requires loading the validator; for the dedupe key a sorted-keys JSON
    form is enough.
    """
    import json

    return f"{target_kind}:{json.dumps(target_config, sort_keys=True)}"


@activity.defn
async def load_latest_snapshots(
    rules: list[RuleSummary],
) -> dict[UUID, MetricSnapshot]:
    """Fetch the most recent value for each rule's target.

    Dedupes queries by ``(target_kind, target_config)`` so two rules with the
    same selector share one round-trip. Rules whose target source returns
    ``None`` are omitted from the result; the evaluator turns that into an
    ERROR row downstream.
    """
    if not rules:
        return {}

    snapshots: dict[UUID, MetricSnapshot] = {}
    cache: dict[str, MetricSnapshot | None] = {}
    async with _session_factory()() as session:
        for rule in rules:
            key = _target_dedupe_key(rule.target_kind, rule.target_config)
            if key in cache:
                snapshot = cache[key]
            else:
                try:
                    kind = AlertTargetKind(rule.target_kind)
                    config = parse_target_config(kind, rule.target_config)
                    source = lookup_value_source(kind)
                    snapshot = await source.load_latest(session, config)
                except Exception:  # noqa: BLE001
                    activity.logger.exception(
                        "Failed to load latest snapshot for rule %s (target %s)",
                        rule.alert_id,
                        rule.target_kind,
                    )
                    snapshot = None
                cache[key] = snapshot
            if snapshot is not None:
                snapshots[rule.alert_id] = snapshot

    activity.logger.info(
        "Loaded %d snapshots across %d rules.", len(snapshots), len(rules)
    )
    return snapshots


def _rule_lookback_seconds(rule: RuleSummary) -> int | None:
    """Return the historical lookback this rule needs, or ``None`` if it needs none.

    Only RoC and stddev rules pull a history; threshold rules return ``None``.
    Invalid rule configs are logged and treated as "no history needed" — the
    evaluator will raise its own ERROR when it tries to evaluate them.
    """
    try:
        kind = AlertRuleKind(rule.rule_kind)
        config = parse_rule_config(kind, rule.rule_config)
    except Exception:  # noqa: BLE001
        activity.logger.exception(
            "Invalid rule_config on rule %s; skipping history load.", rule.alert_id
        )
        return None
    if isinstance(config, RateOfChangeRuleConfig):
        return config.window_seconds
    if isinstance(config, StddevDeviationRuleConfig):
        return config.lookback_seconds
    return None


def _index_rules_by_target(
    rules: list[RuleSummary],
) -> tuple[
    dict[str, int],
    dict[str, TargetConfig],
    dict[str, AlertTargetKind],
    dict[UUID, tuple[str, int]],
]:
    """Group rules by ``(target_kind, target_config)`` and pick the max lookback.

    Returns four maps:
    * ``lookback_by_key``: max lookback to load per dedupe key.
    * ``config_by_key``: parsed target config per dedupe key.
    * ``kind_by_key``: target kind per dedupe key.
    * ``rule_lookbacks``: per-rule (key, lookback) so the slicing step can
      narrow the shared series to each rule's own window.
    """
    lookback_by_key: dict[str, int] = {}
    config_by_key: dict[str, TargetConfig] = {}
    kind_by_key: dict[str, AlertTargetKind] = {}
    rule_lookbacks: dict[UUID, tuple[str, int]] = {}

    for rule in rules:
        lookback = _rule_lookback_seconds(rule)
        if lookback is None:
            continue
        key = _target_dedupe_key(rule.target_kind, rule.target_config)
        lookback_by_key[key] = max(lookback_by_key.get(key, 0), lookback)
        rule_lookbacks[rule.alert_id] = (key, lookback)
        if key in config_by_key:
            continue
        try:
            tk = AlertTargetKind(rule.target_kind)
            config_by_key[key] = parse_target_config(tk, rule.target_config)
            kind_by_key[key] = tk
        except Exception:  # noqa: BLE001
            activity.logger.exception(
                "Invalid target_config on rule %s; skipping history load.",
                rule.alert_id,
            )

    return lookback_by_key, config_by_key, kind_by_key, rule_lookbacks


@activity.defn
async def load_historical_series(
    rules: list[RuleSummary],
) -> dict[UUID, HistoricalSeries]:
    """Load a historical sample range for each rule that needs one.

    Only ``RATE_OF_CHANGE`` and ``STDDEV_DEVIATION`` need history. Lookback
    is taken from the rule config; multiple rules on the same target share a
    single query, sized to the largest lookback any of them asked for.
    Threshold-only rules contribute no entry.
    """
    if not rules:
        return {}

    lookback_by_key, config_by_key, kind_by_key, rule_lookbacks = (
        _index_rules_by_target(rules)
    )
    if not lookback_by_key:
        return {}

    series_by_key: dict[str, HistoricalSeries] = {}
    async with _session_factory()() as session:
        for key, lookback in lookback_by_key.items():
            tk = kind_by_key.get(key)
            cfg = config_by_key.get(key)
            if tk is None or cfg is None:
                continue
            try:
                source = lookup_value_source(tk)
                series_by_key[key] = await source.load_series(session, cfg, lookback)
            except Exception:  # noqa: BLE001
                activity.logger.exception(
                    "Failed to load historical series for target %s (key=%s)",
                    tk.value,
                    key,
                )
                series_by_key[key] = HistoricalSeries(samples=[])

    # Slice per rule: drop samples outside this rule's own (narrower) window.
    out: dict[UUID, HistoricalSeries] = {}
    now = datetime.now(UTC)
    for rule_id, (key, lookback) in rule_lookbacks.items():
        full = series_by_key.get(key)
        if full is None:
            out[rule_id] = HistoricalSeries(samples=[])
            continue
        cutoff = now - timedelta(seconds=lookback)
        out[rule_id] = HistoricalSeries(
            samples=[(t, v) for (t, v) in full.samples if t >= cutoff]
        )

    activity.logger.info(
        "Loaded historical series for %d rules across %d dedupe keys.",
        len(out),
        len(series_by_key),
    )
    return out


@activity.defn
async def load_previous_status(
    keys: list[tuple[UUID, UUID]],
) -> dict[str, str]:
    """Return the latest history status for each (alert_id, team_id) pair.

    Empty dict entry ⇒ no prior history ⇒ treated as ``OK`` by the evaluator.
    """
    if not keys:
        return {}
    result: dict[str, str] = {}
    async with _session_factory()() as session:
        for alert_id, team_id in keys:
            row = (
                await session.execute(
                    select(AlertHistory)
                    .where(
                        AlertHistory.alert_id == alert_id,
                        AlertHistory.team_id == team_id,
                    )
                    .order_by(desc(AlertHistory.evaluated_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is not None:
                result[f"{alert_id}:{team_id}"] = row.status.value
    return result


# ---------------------------------------------------------------------------
# Pure evaluator
# ---------------------------------------------------------------------------


@activity.defn
async def evaluate_rules(payload: EvaluatorInput) -> list[EvaluationEvent]:
    """Pure evaluator. Decides which rules transitioned this tick.

    No I/O. For each rule, looks up its latest snapshot + historical series by
    ``rule_id`` and runs the matching predicate.

    Edge-trigger semantics:

    - ``OK → TRIGGERED``: emit a ``TRIGGERED`` event.
    - ``TRIGGERED → OK``: emit a ``RECOVERED`` event.
    - Stable states: skip; no row.
    - Missing / stale snapshot or evaluator failure: emit an ``ERROR`` event.
    """
    events: list[EvaluationEvent] = []
    now = datetime.now(UTC)
    for rule in payload.rules:
        previous = payload.previous_status.get(
            f"{rule.alert_id}:{rule.team_id}", AlertHistoryStatus.OK.value
        )
        snapshot = payload.snapshots_by_rule.get(rule.alert_id)
        if snapshot is None:
            events.append(_error_event(rule, "No snapshot available", now))
            continue
        if (now - snapshot.observed_at) > MAX_SNAPSHOT_AGE:
            events.append(
                _error_event(
                    rule,
                    f"Snapshot is stale ({(now - snapshot.observed_at).total_seconds():.0f}s old)",
                    now,
                    snapshot=snapshot,
                )
            )
            continue
        try:
            triggered, threshold = _evaluate_one(
                rule, snapshot, payload.historical_series_by_rule.get(rule.alert_id)
            )
        except (ValueError, KeyError) as exc:
            events.append(_error_event(rule, str(exc), now, snapshot=snapshot))
            continue
        if triggered and previous != AlertHistoryStatus.TRIGGERED.value:
            events.append(
                _transition_event(
                    rule,
                    snapshot,
                    AlertHistoryStatus.TRIGGERED.value,
                    threshold,
                    now,
                )
            )
        elif not triggered and previous == AlertHistoryStatus.TRIGGERED.value:
            events.append(
                _transition_event(
                    rule,
                    snapshot,
                    AlertHistoryStatus.RECOVERED.value,
                    threshold,
                    now,
                )
            )
    activity.logger.info(
        "Evaluated %d rules; %d events emitted.",
        len(payload.rules),
        len(events),
    )
    return events


def _evaluate_one(
    rule: RuleSummary,
    snapshot: MetricSnapshot,
    series: HistoricalSeries | None,
) -> tuple[bool, float | None]:
    """Run the rule's predicate against the latest snapshot (and history if needed).

    Returns:
        ``(triggered, threshold)``. ``threshold`` is the reference value
        recorded onto the history row so the UI can show context — its
        meaning depends on the rule kind (configured threshold for
        THRESHOLD; configured magnitude for RATE_OF_CHANGE; historical mean
        for STDDEV_DEVIATION).

    Raises:
        ValueError: For unsupported configurations, zero baselines, or a
            short historical series.
    """
    kind = AlertRuleKind(rule.rule_kind)
    config = parse_rule_config(kind, rule.rule_config)
    if isinstance(config, ThresholdRuleConfig):
        return _check_threshold(snapshot.value, config), config.value
    if isinstance(config, RateOfChangeRuleConfig):
        if series is None or not series.samples:
            msg = "No historical samples available for rate-of-change."
            raise ValueError(msg)
        triggered = _check_rate_of_change(snapshot, config, series)
        return triggered, config.delta_pct
    if isinstance(config, StddevDeviationRuleConfig):
        if series is None or len(series.samples) < MIN_STDDEV_SAMPLES:
            msg = (
                f"Insufficient history for stddev: "
                f"{0 if series is None else len(series.samples)} samples, "
                f"need {MIN_STDDEV_SAMPLES}."
            )
            raise ValueError(msg)
        return _check_stddev_deviation(snapshot, config, series)
    msg = f"Unsupported rule kind {kind!r}"  # type: ignore[unreachable]
    raise ValueError(msg)


def _check_threshold(value: float, config: ThresholdRuleConfig) -> bool:
    op = config.operator
    target = config.value
    if op == ">":
        return value > target
    if op == ">=":
        return value >= target
    if op == "<":
        return value < target
    if op == "<=":
        return value <= target
    if op == "==":
        return value == target
    if op == "!=":
        return value != target
    msg = f"Unknown threshold operator {op!r}"
    raise ValueError(msg)


def _check_rate_of_change(
    snapshot: MetricSnapshot,
    config: RateOfChangeRuleConfig,
    series: HistoricalSeries,
) -> bool:
    """Compare current vs the sample closest to ``observed_at - window_seconds``.

    Applies the direction rule to ``(current - past) / |past| x 100``. A zero
    baseline is undefined and raises so the evaluator emits an ERROR row.
    """
    target_time = snapshot.observed_at - timedelta(seconds=config.window_seconds)
    closest = min(
        series.samples,
        key=lambda s: abs((s[0] - target_time).total_seconds()),
    )
    _, past_value = closest
    if past_value == 0:
        msg = "Zero baseline: rate-of-change undefined."
        raise ValueError(msg)
    pct_change = (snapshot.value - past_value) / abs(past_value) * 100.0
    if config.direction == "above":
        return pct_change > config.delta_pct
    if config.direction == "below":
        return pct_change < -config.delta_pct
    return abs(pct_change) > config.delta_pct


def _check_stddev_deviation(
    snapshot: MetricSnapshot,
    config: StddevDeviationRuleConfig,
    series: HistoricalSeries,
) -> tuple[bool, float]:
    """Compare current value against the historical mean +/- ``multiplier x stddev``.

    Returns ``(triggered, mean)`` so the history row captures the band centre
    that drove the decision.
    """
    values = [v for _, v in series.samples]
    mean = statistics.fmean(values)
    stddev = statistics.pstdev(values)
    if stddev == 0.0:
        # Flat series: no deviation possible, nothing to fire on.
        return False, mean
    margin = config.multiplier * stddev
    if config.direction == "above":
        triggered = snapshot.value > mean + margin
    elif config.direction == "below":
        triggered = snapshot.value < mean - margin
    else:
        triggered = abs(snapshot.value - mean) > margin
    return triggered, mean


def _transition_event(
    rule: RuleSummary,
    snapshot: MetricSnapshot,
    status: str,
    threshold: float | None,
    now: datetime,
) -> EvaluationEvent:
    return EvaluationEvent(
        alert_id=rule.alert_id,
        team_id=rule.team_id,
        status=status,
        metric_value=snapshot.value,
        threshold=threshold,
        message=f"{rule.name} {status.lower()}",
        context={
            "snapshotId": str(snapshot.snapshot_id) if snapshot.snapshot_id else None,
            "snapshotTable": snapshot.snapshot_table,
            "evaluatorVersion": "2",
        },
        evaluated_at=now,
        integration_ids=rule.integration_ids,
    )


def _error_event(
    rule: RuleSummary,
    reason: str,
    now: datetime,
    *,
    snapshot: MetricSnapshot | None = None,
) -> EvaluationEvent:
    return EvaluationEvent(
        alert_id=rule.alert_id,
        team_id=rule.team_id,
        status=AlertHistoryStatus.ERROR.value,
        metric_value=snapshot.value if snapshot else None,
        threshold=None,
        message=reason,
        context={
            "snapshotId": (
                str(snapshot.snapshot_id) if snapshot and snapshot.snapshot_id else None
            ),
            "snapshotTable": snapshot.snapshot_table if snapshot else None,
            "evaluatorVersion": "2",
            "notes": reason,
        },
        evaluated_at=now,
        integration_ids=rule.integration_ids,
    )


# ---------------------------------------------------------------------------
# Persistence + enqueue
# ---------------------------------------------------------------------------


@activity.defn
async def persist_evaluation_results(events: list[EvaluationEvent]) -> list[UUID]:
    """Insert ``alert_history`` rows for each transition event."""
    if not events:
        return []
    history_ids: list[UUID] = []
    async with _session_factory()() as session:
        for event in events:
            history = AlertHistory(
                alert_id=event.alert_id,
                team_id=event.team_id,
                status=AlertHistoryStatus(event.status),
                metric_value=event.metric_value,
                threshold=event.threshold,
                message=event.message,
                context=event.context,
                evaluated_at=event.evaluated_at,
            )
            session.add(history)
            await session.flush()
            history_ids.append(history.id)
        await session.commit()
    activity.logger.info("Persisted %d alert_history rows.", len(history_ids))
    return history_ids


@activity.defn
async def enqueue_notifications(
    events_with_ids: list[tuple[EvaluationEvent, UUID]],
) -> int:
    """Insert one ``notification`` row per (event, integration) pair.

    ERROR events are *not* notified — the team will see them in the history
    page but we don't spam mailboxes with infrastructure errors.
    """
    if not events_with_ids:
        return 0
    inserted = 0
    async with _session_factory()() as session:
        for event, history_id in events_with_ids:
            if event.status == AlertHistoryStatus.ERROR.value:
                continue
            for integration_id in event.integration_ids:
                session.add(
                    Notification(
                        alert_history_id=history_id,
                        integration_id=integration_id,
                        status=NotificationStatus.PENDING,
                    )
                )
                inserted += 1
        await session.commit()
    activity.logger.info("Enqueued %d notifications.", inserted)
    return inserted


# ---------------------------------------------------------------------------
# Dispatcher activities
# ---------------------------------------------------------------------------


@activity.defn
async def claim_pending_notifications(
    batch_size: int = DEFAULT_DISPATCH_BATCH,
) -> list[UUID]:
    """Mark up to ``batch_size`` pending notifications as ``RETRYING`` and return their IDs.

    Done in two steps because Postgres rejects ``FOR UPDATE`` over the nullable
    side of an outer join, and ``Notification.history`` / ``Notification.integration``
    are eager-joined (``lazy="joined"``). Step 1 locks just the primary keys —
    no joins added — then step 2 bulk-updates by ``id IN (...)``.
    """
    async with _session_factory()() as session:
        id_rows = (
            await session.execute(
                select(Notification.id)
                .where(
                    Notification.status.in_(
                        [NotificationStatus.PENDING, NotificationStatus.RETRYING]
                    )
                )
                .order_by(Notification.created_at)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
        ids: list[UUID] = [row[0] for row in id_rows]
        if ids:
            await session.execute(
                update(Notification)
                .where(Notification.id.in_(ids))
                .values(
                    status=NotificationStatus.RETRYING,
                    attempt_count=Notification.attempt_count + 1,
                )
            )
        await session.commit()
    activity.logger.info("Claimed %d notifications for dispatch.", len(ids))
    return ids


@activity.defn
async def dispatch_notification(notification_id: UUID) -> DispatchOutcome:
    """Send one notification through its integration."""
    async with _session_factory()() as session:
        notification = await session.get(Notification, notification_id)
        if notification is None:
            return DispatchOutcome(
                notification_id=notification_id,
                success=False,
                error="Notification row vanished before dispatch.",
            )
        integration = await session.get(AlertIntegration, notification.integration_id)
        history = await session.get(AlertHistory, notification.alert_history_id)
        alert = await session.get(Alert, history.alert_id) if history else None
        if integration is None or history is None or alert is None:
            return DispatchOutcome(
                notification_id=notification_id,
                success=False,
                error="Missing related row (integration / history / alert).",
            )
        kind = AlertIntegrationKind(integration.kind)
        config = parse_integration_config(kind, integration.config)

    if isinstance(config, EmailIntegrationConfig):
        return await _dispatch_email(notification_id, alert, history, config)
    if isinstance(config, WebhookIntegrationConfig):
        return await _dispatch_webhook(notification_id, alert, history, config)
    return DispatchOutcome(  # type: ignore[unreachable]
        notification_id=notification_id,
        success=False,
        error=f"Unsupported integration kind {kind.value}.",
    )


def _email_chain_token_labels(alert: Alert) -> tuple[str | None, str | None]:
    """Return ``(chain_label, token_label)`` for the email template.

    Metric targets surface their chain / token straight from the
    ``MetricTargetConfig``. Market targets surface the protocol slug as the
    "chain" and the market hex / label as the "token", so the existing email
    template (which renders both as optional context lines) keeps working
    without a template refactor.
    """
    try:
        kind = AlertTargetKind(alert.target_kind.value)
        config = parse_target_config(kind, dict(alert.target_config))
    except Exception:  # noqa: BLE001
        return None, None
    if isinstance(config, MetricTargetConfig):
        return (
            config.chain.value if config.chain else None,
            config.token.value if config.token else None,
        )
    # Market targets — surface protocol + market id so the email subject line
    # still has context.
    return (
        f"market:{config.market_config_id}",
        f"{config.chain_id}:{config.market_id_hex}",
    )


async def _dispatch_email(
    notification_id: UUID,
    alert: Alert,
    history: AlertHistory,
    config: EmailIntegrationConfig,
) -> DispatchOutcome:
    """Render and send an email via the existing ``EmailMessageService``."""
    evaluated_at = history.evaluated_at.isoformat()
    chain, token = _email_chain_token_labels(alert)
    try:
        async with get_email_config().provide_service() as mailer:
            email_service = EmailMessageService(mailer=mailer)
            recipients: Iterable[str] = [config.to, *config.cc]
            for recipient in recipients:
                if history.status == AlertHistoryStatus.RECOVERED:
                    sent = await email_service.send_alert_recovered_email(
                        to_email=recipient,
                        alert_name=alert.name,
                        chain=chain,
                        token=token,
                        metric_value=history.metric_value,
                        evaluated_at=evaluated_at,
                    )
                else:
                    sent = await email_service.send_alert_triggered_email(
                        to_email=recipient,
                        alert_name=alert.name,
                        severity=alert.severity.value,
                        chain=chain,
                        token=token,
                        metric_value=history.metric_value,
                        threshold=history.threshold,
                        message=history.message,
                        evaluated_at=evaluated_at,
                    )
                if not sent:
                    return DispatchOutcome(
                        notification_id=notification_id,
                        success=False,
                        error=f"Email delivery failed for {recipient}.",
                    )
    except Exception as exc:  # noqa: BLE001 — surface the full reason to last_error
        return DispatchOutcome(
            notification_id=notification_id,
            success=False,
            error=f"Email exception: {exc.__class__.__name__}: {exc}",
        )
    return DispatchOutcome(notification_id=notification_id, success=True)


async def _dispatch_webhook(
    notification_id: UUID,
    alert: Alert,
    history: AlertHistory,
    config: WebhookIntegrationConfig,
) -> DispatchOutcome:
    """POST a signed JSON payload to the webhook URL."""
    secret = (
        decrypt_secret(config.secret) if is_encrypted(config.secret) else config.secret
    )
    payload = {
        "eventId": str(history.id),
        "alertHistoryId": str(history.id),
        "alertId": str(alert.id),
        "teamId": str(history.team_id),
        "name": alert.name,
        "status": history.status.value,
        "severity": alert.severity.value,
        "targetKind": alert.target_kind.value,
        "targetConfig": dict(alert.target_config),
        "metricValue": history.metric_value,
        "threshold": history.threshold,
        "message": history.message,
        "evaluatedAt": history.evaluated_at.isoformat(),
    }
    success, error = await deliver_webhook(
        config.url,
        secret,
        payload,
        extra_headers=config.headers,
    )
    return DispatchOutcome(
        notification_id=notification_id,
        success=success,
        error=error,
    )


@activity.defn
async def mark_notification_sent(notification_id: UUID) -> None:
    """Transition a notification row to ``SENT`` and record ``sent_at``."""
    async with _session_factory()() as session:
        notification = await session.get(Notification, notification_id)
        if notification is None:
            return
        notification.status = NotificationStatus.SENT
        notification.sent_at = datetime.now(UTC)
        notification.last_error = None
        await session.commit()


@activity.defn
async def mark_notification_failed(
    notification_id: UUID,
    error: str,
) -> None:
    """Decide whether a failed delivery becomes ``RETRYING`` or ``FAILED``."""
    async with _session_factory()() as session:
        notification = await session.get(Notification, notification_id)
        if notification is None:
            return
        notification.last_error = error[:1000]
        if notification.attempt_count >= MAX_NOTIFICATION_ATTEMPTS:
            notification.status = NotificationStatus.FAILED
        else:
            notification.status = NotificationStatus.RETRYING
        await session.commit()
