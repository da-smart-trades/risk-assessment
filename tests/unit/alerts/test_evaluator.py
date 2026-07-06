# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unit tests for the pure evaluator paths.

The activity wrappers (``load_*``, ``persist_*``) are I/O and live behind a
session factory — they're exercised by integration tests, not here. These
tests target the pure helpers that decide whether a rule fires.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cert_ra.alerts.activities import (
    MIN_STDDEV_SAMPLES,
    _check_rate_of_change,
    _check_stddev_deviation,
    _check_threshold,
    _evaluate_one,
)
from cert_ra.alerts.schemas import HistoricalSeries, MetricSnapshot, RuleSummary
from cert_ra.api.domain.alerts.rules import (
    RateOfChangeRuleConfig,
    StddevDeviationRuleConfig,
    ThresholdRuleConfig,
)
from cert_ra.types import AlertRuleKind, AlertTargetKind

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot(value: float, *, at: datetime | None = None) -> MetricSnapshot:
    return MetricSnapshot(
        value=value,
        observed_at=at or datetime.now(UTC),
        snapshot_id=None,
        snapshot_table="test",
    )


def _rule_summary(
    *,
    rule_kind: AlertRuleKind,
    rule_config: dict,
    target_kind: AlertTargetKind = AlertTargetKind.METRIC,
    target_config: dict | None = None,
) -> RuleSummary:
    return RuleSummary(
        alert_id=uuid4(),
        team_id=uuid4(),
        is_template=False,
        name="test rule",
        severity="WARNING",
        target_kind=target_kind.value,
        target_config=target_config or {"type": "METRIC", "metricType": "GAS_PRICE"},
        rule_kind=rule_kind.value,
        rule_config=rule_config,
        integration_ids=[],
    )


# ---------------------------------------------------------------------------
# Threshold
# ---------------------------------------------------------------------------


def test_check_threshold_greater_than_fires_when_value_exceeds() -> None:
    config = ThresholdRuleConfig(operator=">", value=100.0)
    assert _check_threshold(150.0, config) is True
    assert _check_threshold(100.0, config) is False
    assert _check_threshold(50.0, config) is False


def test_check_threshold_supports_all_operators() -> None:
    for op, value, expected in [
        (">=", 100.0, True),
        ("<", 50.0, True),
        ("<=", 100.0, True),
        ("==", 100.0, True),
        ("!=", 99.0, True),
    ]:
        config = ThresholdRuleConfig(operator=op, value=100.0)
        assert _check_threshold(value, config) is expected


# ---------------------------------------------------------------------------
# Rate of change
# ---------------------------------------------------------------------------


def test_rate_of_change_above_fires_on_sufficient_rise() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    snapshot = _snapshot(120.0, at=now)
    config = RateOfChangeRuleConfig(
        delta_pct=10.0, window_seconds=3600, direction="above"
    )
    series = HistoricalSeries(samples=[(now - timedelta(seconds=3600), 100.0)])
    assert _check_rate_of_change(snapshot, config, series) is True


def test_rate_of_change_above_does_not_fire_on_drop() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    snapshot = _snapshot(80.0, at=now)
    config = RateOfChangeRuleConfig(
        delta_pct=10.0, window_seconds=3600, direction="above"
    )
    series = HistoricalSeries(samples=[(now - timedelta(seconds=3600), 100.0)])
    assert _check_rate_of_change(snapshot, config, series) is False


def test_rate_of_change_below_fires_on_drop() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    snapshot = _snapshot(80.0, at=now)
    config = RateOfChangeRuleConfig(
        delta_pct=10.0, window_seconds=3600, direction="below"
    )
    series = HistoricalSeries(samples=[(now - timedelta(seconds=3600), 100.0)])
    assert _check_rate_of_change(snapshot, config, series) is True


def test_rate_of_change_both_fires_on_either_direction() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    config = RateOfChangeRuleConfig(
        delta_pct=10.0, window_seconds=3600, direction="both"
    )
    series = HistoricalSeries(samples=[(now - timedelta(seconds=3600), 100.0)])
    assert _check_rate_of_change(_snapshot(120.0, at=now), config, series) is True
    assert _check_rate_of_change(_snapshot(80.0, at=now), config, series) is True
    assert _check_rate_of_change(_snapshot(105.0, at=now), config, series) is False


def test_rate_of_change_picks_sample_closest_to_target_time() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    snapshot = _snapshot(110.0, at=now)
    config = RateOfChangeRuleConfig(
        delta_pct=5.0, window_seconds=3600, direction="above"
    )
    series = HistoricalSeries(
        samples=[
            (now - timedelta(seconds=7200), 50.0),  # too old
            (now - timedelta(seconds=3300), 100.0),  # closest to target
            (now - timedelta(seconds=300), 109.0),  # too recent
        ]
    )
    assert _check_rate_of_change(snapshot, config, series) is True


def test_rate_of_change_zero_baseline_raises() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    snapshot = _snapshot(10.0, at=now)
    config = RateOfChangeRuleConfig(
        delta_pct=10.0, window_seconds=3600, direction="above"
    )
    series = HistoricalSeries(samples=[(now - timedelta(seconds=3600), 0.0)])
    with pytest.raises(ValueError, match="Zero baseline"):
        _check_rate_of_change(snapshot, config, series)


# ---------------------------------------------------------------------------
# Stddev deviation
# ---------------------------------------------------------------------------


def _flat_then_spike_series(*, baseline: float, spike: float, count: int) -> HistoricalSeries:
    base_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    samples = [
        (base_time + timedelta(seconds=i), baseline + (i % 3 - 1) * 0.5)
        for i in range(count)
    ]
    samples[-1] = (samples[-1][0], spike)
    return HistoricalSeries(samples=samples)


def test_stddev_above_fires_on_spike() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    snapshot = _snapshot(150.0, at=now)
    series = _flat_then_spike_series(baseline=100.0, spike=100.5, count=20)
    config = StddevDeviationRuleConfig(
        multiplier=2.0, lookback_seconds=3600, direction="above"
    )
    triggered, mean = _check_stddev_deviation(snapshot, config, series)
    assert triggered is True
    assert 99.0 < mean < 101.0


def test_stddev_below_fires_on_dip() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    snapshot = _snapshot(50.0, at=now)
    series = _flat_then_spike_series(baseline=100.0, spike=100.5, count=20)
    config = StddevDeviationRuleConfig(
        multiplier=2.0, lookback_seconds=3600, direction="below"
    )
    triggered, _ = _check_stddev_deviation(snapshot, config, series)
    assert triggered is True


def test_stddev_both_fires_on_either_extreme() -> None:
    series = _flat_then_spike_series(baseline=100.0, spike=100.5, count=20)
    config = StddevDeviationRuleConfig(
        multiplier=2.0, lookback_seconds=3600, direction="both"
    )
    assert _check_stddev_deviation(_snapshot(150.0), config, series)[0] is True
    assert _check_stddev_deviation(_snapshot(50.0), config, series)[0] is True
    assert _check_stddev_deviation(_snapshot(100.5), config, series)[0] is False


def test_stddev_flat_series_does_not_fire() -> None:
    base_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    series = HistoricalSeries(
        samples=[(base_time + timedelta(seconds=i), 100.0) for i in range(20)]
    )
    config = StddevDeviationRuleConfig(
        multiplier=2.0, lookback_seconds=3600, direction="both"
    )
    triggered, mean = _check_stddev_deviation(_snapshot(150.0), config, series)
    assert triggered is False
    assert mean == 100.0


def test_stddev_short_series_raises_via_evaluate_one() -> None:
    """``_evaluate_one`` enforces the MIN_STDDEV_SAMPLES floor."""
    rule = _rule_summary(
        rule_kind=AlertRuleKind.STDDEV_DEVIATION,
        rule_config={
            "type": "STDDEV_DEVIATION",
            "multiplier": 1.0,
            "lookbackSeconds": 3600,
            "direction": "both",
        },
    )
    snapshot = _snapshot(150.0)
    short_series = HistoricalSeries(
        samples=[
            (datetime(2026, 1, 1, 12, 0, tzinfo=UTC), 100.0)
            for _ in range(MIN_STDDEV_SAMPLES - 1)
        ]
    )
    with pytest.raises(ValueError, match="Insufficient history"):
        _evaluate_one(rule, snapshot, short_series)


# ---------------------------------------------------------------------------
# _evaluate_one dispatch
# ---------------------------------------------------------------------------


def test_evaluate_one_threshold_returns_configured_value() -> None:
    rule = _rule_summary(
        rule_kind=AlertRuleKind.THRESHOLD,
        rule_config={"type": "THRESHOLD", "operator": ">", "value": 100.0},
    )
    triggered, threshold = _evaluate_one(rule, _snapshot(150.0), None)
    assert triggered is True
    assert threshold == 100.0


def test_evaluate_one_rate_of_change_requires_history() -> None:
    rule = _rule_summary(
        rule_kind=AlertRuleKind.RATE_OF_CHANGE,
        rule_config={
            "type": "RATE_OF_CHANGE",
            "deltaPct": 10.0,
            "windowSeconds": 3600,
            "direction": "above",
        },
    )
    with pytest.raises(ValueError, match="No historical samples"):
        _evaluate_one(rule, _snapshot(150.0), None)
