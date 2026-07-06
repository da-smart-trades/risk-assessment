# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Pure-function tests for the market PD calculator.

Target: 100% branch coverage on ``compute_market_pd``. The calculator
takes simple shaped inputs (a score dict, a list of weighting profile
entries, a list of manual-metric rows) and returns a deterministic
:class:`PdBreakdown` — so tests can exercise every code path without
mocking I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from cert_ra.metrics.market.scoring import (
    MarketScoringError,
    compute_market_pd,
)
from cert_ra.types import WeightingProfileEntryCategory

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeEntry:
    """Drop-in for ``WeightingProfileEntry`` with the fields the calc reads."""

    id: UUID
    category: WeightingProfileEntryCategory
    sub_category: str
    weight: Decimal


def _entry(
    category: WeightingProfileEntryCategory,
    sub: str,
    weight: float | Decimal,
) -> _FakeEntry:
    return _FakeEntry(
        id=uuid4(),
        category=category,
        sub_category=sub,
        weight=Decimal(str(weight)),
    )


@dataclass
class _FakeManualMetric:
    """Drop-in for ``ManualMetric`` with the fields the calc reads.

    The assurance dimension (and weight key) is ``name``; ``sub_category``
    only marks the Evidence/Multiplier pair and is ignored by the calc.
    """

    id: UUID
    value: str | None
    name: str | None = None
    sub_category: str | None = "Multiplier"
    notes: str | None = None


def _assurance(sub: str, value: float | str | None) -> _FakeManualMetric:
    # ``sub`` is the dimension — store it as ``name`` (the weight key) and
    # keep ``sub_category`` on the literal "Multiplier" the real rows carry.
    return _FakeManualMetric(
        id=uuid4(),
        value=None if value is None else str(value),
        name=sub,
        sub_category="Multiplier",
    )


def _anchor(
    name: str,
    value: float | str | None,
    *,
    sub_category: str | None = None,
    notes: str | None = None,
) -> _FakeManualMetric:
    """A manual ANCHORS row: ``value`` is the pd; key is sub_category or name."""
    return _FakeManualMetric(
        id=uuid4(),
        value=None if value is None else str(value),
        name=name,
        sub_category=sub_category,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Empty-input rule (PRD: each empty term forces to 1.0)
# ---------------------------------------------------------------------------


def test_compute_returns_neutral_when_all_inputs_empty() -> None:
    result = compute_market_pd(None, [], [])
    assert result.final_pd == 1.0
    assert result.anchors_term == 1.0
    assert result.control_term == 1.0
    assert result.assurance_term == 1.0


def test_compute_handles_empty_score_dict() -> None:
    result = compute_market_pd({}, [], [])
    assert result.final_pd == 1.0


def test_compute_empty_anchors_forces_anchors_term_to_one() -> None:
    """Anchors absent → term=1, so final = control × assurance."""
    result = compute_market_pd(
        {"controlModifiers": {"k": {"multiplier": 0.9}}},
        [],
        [_assurance("trustworthiness", 1.1)],
    )
    assert result.anchors_term == 1.0
    assert result.final_pd == pytest.approx(0.9 * 1.1)


def test_compute_empty_controls_forces_control_term_to_one() -> None:
    result = compute_market_pd(
        {"anchors": {"k": {"pd": 0.4}}}, [], [_assurance("trust", 1.05)]
    )
    assert result.control_term == 1.0
    assert result.final_pd == pytest.approx(0.4 * 1.05)


def test_compute_empty_assurances_forces_assurance_term_to_one() -> None:
    result = compute_market_pd(
        {
            "anchors": {"k": {"pd": 0.2}},
            "controlModifiers": {"m": {"multiplier": 0.95}},
        },
        [],
        [],
    )
    assert result.assurance_term == 1.0
    assert result.final_pd == pytest.approx(0.2 * 0.95)


def test_compute_skips_assurance_rows_with_no_value() -> None:
    """Operator hasn't filled in a multiplier yet — treat the row as neutral."""
    result = compute_market_pd(
        None, [], [_assurance("trust", None), _assurance("audit", "")]
    )
    assert result.assurance_term == 1.0


# ---------------------------------------------------------------------------
# Anchors term: 1 − ∏(1 − pd × w)
# ---------------------------------------------------------------------------


def test_anchors_single_pd_half_yields_half() -> None:
    """pd=0.5, w=1 → 1 − (1 − 0.5) = 0.5."""
    result = compute_market_pd({"anchors": {"k": {"pd": 0.5}}}, [], [])
    assert result.anchors_term == pytest.approx(0.5)


def test_anchors_independent_failures_combine_correctly() -> None:
    """pd=0.3, pd=0.2 → 1 − (0.7 × 0.8) = 0.44."""
    result = compute_market_pd(
        {"anchors": {"a": {"pd": 0.3}, "b": {"pd": 0.2}}}, [], []
    )
    assert result.anchors_term == pytest.approx(0.44)


def test_anchors_weight_modifies_per_metric_contribution() -> None:
    """pd=0.5, w=0.5 → 1 − (1 − 0.25) = 0.25."""
    entries = [_entry(WeightingProfileEntryCategory.ANCHOR, "k", 0.5)]
    result = compute_market_pd({"anchors": {"k": {"pd": 0.5}}}, entries, [])
    assert result.anchors_term == pytest.approx(0.25)


def test_anchors_zero_pd_contributes_nothing() -> None:
    """pd=0 → factor 1, term contribution is neutral."""
    result = compute_market_pd(
        {"anchors": {"safe": {"pd": 0.0}, "risky": {"pd": 0.5}}}, [], []
    )
    assert result.anchors_term == pytest.approx(0.5)


def test_anchors_pd_at_one_rejected() -> None:
    """A pd of exactly 1.0 isn't a valid probability for this model."""
    with pytest.raises(MarketScoringError, match=r"in \[0, 1\)"):
        compute_market_pd({"anchors": {"k": {"pd": 1.0}}}, [], [])


def test_anchors_pd_above_one_rejected() -> None:
    with pytest.raises(MarketScoringError):
        compute_market_pd({"anchors": {"k": {"pd": 1.5}}}, [], [])


def test_anchors_negative_pd_rejected() -> None:
    with pytest.raises(MarketScoringError):
        compute_market_pd({"anchors": {"k": {"pd": -0.1}}}, [], [])


def test_anchors_non_numeric_pd_rejected() -> None:
    with pytest.raises(MarketScoringError, match="expected a number"):
        compute_market_pd({"anchors": {"k": {"pd": "high"}}}, [], [])


def test_anchors_boolean_pd_rejected() -> None:
    """A literal ``true`` is technically an int(1.0) in Python — guard."""
    with pytest.raises(MarketScoringError, match="boolean"):
        compute_market_pd({"anchors": {"k": {"pd": True}}}, [], [])


def test_anchors_payload_not_a_dict_rejected() -> None:
    with pytest.raises(MarketScoringError, match="expected an object"):
        compute_market_pd({"anchors": {"k": [1, 2]}}, [], [])


def test_anchors_top_level_not_a_dict_rejected() -> None:
    with pytest.raises(MarketScoringError, match="must be an object"):
        compute_market_pd({"anchors": "broken"}, [], [])


# ---------------------------------------------------------------------------
# Control modifiers term: clamp(∏(multiplier × w))
# ---------------------------------------------------------------------------


def test_controls_neutral_multiplier_yields_one() -> None:
    result = compute_market_pd({"controlModifiers": {"k": {"multiplier": 1.0}}}, [], [])
    assert result.control_term == 1.0


def test_controls_reads_new_controls_key() -> None:
    """The scorer's current ``controls`` key is read identically to the legacy one."""
    new = compute_market_pd({"controls": {"k": {"multiplier": 0.9}}}, [], [])
    legacy = compute_market_pd({"controlModifiers": {"k": {"multiplier": 0.9}}}, [], [])
    assert new.control_term == legacy.control_term
    assert new.control_term != 1.0


def test_controls_two_multipliers_compound() -> None:
    """0.9 × 1.1 = 0.99 (inside clamp range)."""
    result = compute_market_pd(
        {
            "controlModifiers": {
                "a": {"multiplier": 0.9},
                "b": {"multiplier": 1.1},
            }
        },
        [],
        [],
    )
    assert result.control_term == pytest.approx(0.99)


def test_controls_clamped_high() -> None:
    """multiplier=2.0 → product 2.0 → clamps to 1.25."""
    result = compute_market_pd({"controlModifiers": {"k": {"multiplier": 2.0}}}, [], [])
    assert result.control_term == 1.25


def test_controls_clamped_low() -> None:
    """multiplier=0.5 → product 0.5 → clamps to 0.75."""
    result = compute_market_pd({"controlModifiers": {"k": {"multiplier": 0.5}}}, [], [])
    assert result.control_term == 0.75


def test_controls_weight_modifies_per_metric_contribution() -> None:
    """multiplier=1.2, w=0.5 → 1.2 × 0.5 = 0.6 → clamps to 0.75."""
    entries = [_entry(WeightingProfileEntryCategory.CONTROL, "k", 0.5)]
    result = compute_market_pd(
        {"controlModifiers": {"k": {"multiplier": 1.2}}}, entries, []
    )
    assert result.control_term == 0.75


def test_controls_negative_multiplier_rejected() -> None:
    with pytest.raises(MarketScoringError, match="non-negative"):
        compute_market_pd({"controlModifiers": {"k": {"multiplier": -0.1}}}, [], [])


def test_controls_non_numeric_multiplier_rejected() -> None:
    with pytest.raises(MarketScoringError, match="expected a number"):
        compute_market_pd({"controlModifiers": {"k": {"multiplier": "high"}}}, [], [])


def test_controls_payload_not_a_dict_rejected() -> None:
    with pytest.raises(MarketScoringError, match="expected an object"):
        compute_market_pd({"controlModifiers": {"k": [1, 2]}}, [], [])


def test_controls_top_level_not_a_dict_rejected() -> None:
    with pytest.raises(MarketScoringError, match="must be an object"):
        compute_market_pd({"controlModifiers": [1, 2]}, [], [])


# ---------------------------------------------------------------------------
# Assurance term: clamp(∏(value × w))
# ---------------------------------------------------------------------------


def test_assurance_single_neutral_value() -> None:
    result = compute_market_pd(None, [], [_assurance("trust", 1.0)])
    assert result.assurance_term == 1.0


def test_assurance_two_multipliers_compound() -> None:
    """1.05 × 0.95 ≈ 0.9975 (in clamp range)."""
    result = compute_market_pd(None, [], [_assurance("a", 1.05), _assurance("b", 0.95)])
    assert result.assurance_term == pytest.approx(1.05 * 0.95)


def test_assurance_clamped_high() -> None:
    """value=2.0 → product 2.0 → clamps to 1.25."""
    result = compute_market_pd(None, [], [_assurance("k", 2.0)])
    assert result.assurance_term == 1.25


def test_assurance_clamped_low() -> None:
    """value=0.5 → product 0.5 → clamps to 0.75."""
    result = compute_market_pd(None, [], [_assurance("k", 0.5)])
    assert result.assurance_term == 0.75


def test_assurance_weight_modifies_contribution() -> None:
    """value=1.2, w=0.5 → 0.6 → clamps to 0.75."""
    entries = [_entry(WeightingProfileEntryCategory.ASSURANCE, "k", 0.5)]
    result = compute_market_pd(None, entries, [_assurance("k", 1.2)])
    assert result.assurance_term == 0.75


def test_assurance_unparseable_value_rejected() -> None:
    metric = _FakeManualMetric(id=uuid4(), value="not a number", name="k")
    with pytest.raises(MarketScoringError, match="not a valid float multiplier"):
        compute_market_pd(None, [], [metric])


def test_assurance_negative_value_rejected() -> None:
    with pytest.raises(MarketScoringError, match="non-negative"):
        compute_market_pd(None, [], [_assurance("k", -0.1)])


def test_assurance_none_name_defaults_to_empty_string() -> None:
    """A row with no ``name`` maps onto the empty-string lookup key.

    Default weight applies (no entry for the empty key), so the row's
    value passes through unchanged.
    """
    metric = _FakeManualMetric(id=uuid4(), value="1.1", name=None)
    result = compute_market_pd(None, [], [metric])
    assert result.assurance_term == 1.1


def test_assurance_weight_keys_on_name_not_sub_category() -> None:
    """A weight targets the dimension ``name``; ``sub_category`` is ignored.

    The row carries ``name='Audits'`` and ``sub_category='Multiplier'``.
    An entry keyed on 'Audits' must apply; one keyed on 'Multiplier' must not.
    """
    metric = _FakeManualMetric(
        id=uuid4(), value="1.2", name="Audits", sub_category="Multiplier"
    )
    # Weight on the dimension name applies: 1.2 × 0.5 = 0.6 → clamps to 0.75.
    by_name = [_entry(WeightingProfileEntryCategory.ASSURANCE, "Audits", 0.5)]
    assert compute_market_pd(None, by_name, [metric]).assurance_term == 0.75
    # Weight on the sub_category value does NOT apply: stays 1.2.
    by_sub = [_entry(WeightingProfileEntryCategory.ASSURANCE, "Multiplier", 0.5)]
    assert compute_market_pd(None, by_sub, [metric]).assurance_term == pytest.approx(
        1.2
    )


# ---------------------------------------------------------------------------
# Weight lookup
# ---------------------------------------------------------------------------


def test_negative_weight_rejected_at_lookup_time() -> None:
    """Even with the DB CHECK, defend against bad data."""
    entries = [_entry(WeightingProfileEntryCategory.ANCHOR, "k", Decimal("-0.5"))]
    with pytest.raises(MarketScoringError, match="negative weight"):
        compute_market_pd({"anchors": {"k": {"pd": 0.5}}}, entries, [])


def test_missing_weight_defaults_to_one() -> None:
    """An entry with a different sub_category doesn't shadow the default."""
    entries = [_entry(WeightingProfileEntryCategory.ANCHOR, "other", 0.1)]
    result = compute_market_pd({"anchors": {"k": {"pd": 0.5}}}, entries, [])
    # k uses default 1.0 → term=0.5
    assert result.anchors_term == pytest.approx(0.5)


def test_weight_only_applies_within_matching_category() -> None:
    """A CONTROL weight for 'k' should NOT modify the anchor 'k' lookup."""
    entries = [_entry(WeightingProfileEntryCategory.CONTROL, "k", 0.5)]
    result = compute_market_pd({"anchors": {"k": {"pd": 0.5}}}, entries, [])
    assert result.anchors_term == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Final PD = anchors × control × assurance
# ---------------------------------------------------------------------------


def test_final_pd_is_product_of_three_terms() -> None:
    """0.5 × 1.05 × 0.95 = 0.49875."""
    result = compute_market_pd(
        {
            "anchors": {"k": {"pd": 0.5}},
            "controlModifiers": {"k": {"multiplier": 1.05}},
        },
        [],
        [_assurance("trust", 0.95)],
    )
    assert result.final_pd == pytest.approx(0.5 * 1.05 * 0.95)


# ---------------------------------------------------------------------------
# Breakdown
# ---------------------------------------------------------------------------


def test_breakdown_includes_per_metric_contributions() -> None:
    result = compute_market_pd(
        {
            "anchors": {"a1": {"pd": 0.1}, "a2": {"pd": 0.2}},
            "controlModifiers": {"c1": {"multiplier": 1.1}},
        },
        [_entry(WeightingProfileEntryCategory.ANCHOR, "a1", 0.5)],
        [_assurance("trust", 1.05)],
    )
    assert len(result.breakdown["anchors"]) == 2
    assert len(result.breakdown["controlModifiers"]) == 1
    assert len(result.breakdown["assurance"]) == 1
    # a1 carries the overridden weight; a2 default 1.0
    weights_by_sub = {
        c["subCategory"]: c["weight"] for c in result.breakdown["anchors"]
    }
    assert weights_by_sub == {"a1": 0.5, "a2": 1.0}


def test_breakdown_carries_per_anchor_score_and_rationale() -> None:
    """The wire payload must expose `score` and `rationale` per anchor.

    The PD math ignores both — they exist purely so the show page can
    render `name · score · pd` and a "Score rationale" table. Missing
    fields surface as `None` rather than raising, so older scorer
    payloads don't break rendering.
    """
    result = compute_market_pd(
        {
            "anchors": {
                "a1": {"pd": 0.1, "score": 0.42, "rationale": "tight oracle"},
                "a2": {"pd": 0.2},
            },
            "controlModifiers": {},
        },
        [],
        [],
    )
    by_sub = {c["subCategory"]: c for c in result.breakdown["anchors"]}
    assert by_sub["a1"]["score"] == 0.42
    assert by_sub["a1"]["rationale"] == "tight oracle"
    assert by_sub["a2"]["score"] is None
    assert by_sub["a2"]["rationale"] is None


def test_breakdown_carries_per_control_rationale() -> None:
    """Control modifiers expose `rationale`; older payloads stay `None`."""
    result = compute_market_pd(
        {
            "anchors": {},
            "controlModifiers": {
                "c1": {"multiplier": 1.05, "rationale": "good guardian"},
                "c2": {"multiplier": 0.95},
            },
        },
        [],
        [],
    )
    by_sub = {c["subCategory"]: c for c in result.breakdown["controlModifiers"]}
    assert by_sub["c1"]["rationale"] == "good guardian"
    assert by_sub["c2"]["rationale"] is None


def test_breakdown_score_field_non_numeric_drops_silently() -> None:
    """Non-numeric `score` is treated as absent — UI shows an em-dash.

    The PD math doesn't read `score`, so a bad value shouldn't be
    fatal. ``rationale`` follows the same logic for non-string values.
    """
    result = compute_market_pd(
        {"anchors": {"a1": {"pd": 0.1, "score": "n/a", "rationale": 42}}},
        [],
        [],
    )
    [row] = result.breakdown["anchors"]
    assert row["score"] is None
    assert row["rationale"] is None


# ---------------------------------------------------------------------------
# Manual ANCHORS metrics: fold into the anchors term at fixed weight 1.0
# ---------------------------------------------------------------------------


def test_manual_anchor_folds_into_anchors_term() -> None:
    """A lone manual anchor with pd=0.2 → term = 1 − (1 − 0.2) = 0.2."""
    result = compute_market_pd(None, [], [], [_anchor("Bridging Risk", 0.2)])
    assert result.anchors_term == pytest.approx(0.2)
    assert result.final_pd == pytest.approx(0.2)


def test_manual_anchor_combines_with_scorer_anchors() -> None:
    """Scorer pd=0.3 and manual pd=0.2 → 1 − (0.7 × 0.8) = 0.44."""
    result = compute_market_pd(
        {"anchors": {"a": {"pd": 0.3}}}, [], [], [_anchor("manualRisk", 0.2)]
    )
    assert result.anchors_term == pytest.approx(0.44)


def test_manual_anchor_is_never_weighted() -> None:
    """A weighting-profile entry on a manual anchor's key has no effect."""
    entries = [_entry(WeightingProfileEntryCategory.ANCHOR, "Bridging Risk", 0.5)]
    weighted = compute_market_pd(None, entries, [], [_anchor("Bridging Risk", 0.5)])
    unweighted = compute_market_pd(None, [], [], [_anchor("Bridging Risk", 0.5)])
    # Both stay at weight 1.0 → term = 0.5 regardless of the profile entry.
    assert weighted.anchors_term == pytest.approx(0.5)
    assert unweighted.anchors_term == pytest.approx(0.5)


def test_manual_anchor_blank_value_skipped() -> None:
    """A manual anchor with no value is neutral (skipped), like assurance."""
    result = compute_market_pd(
        {"anchors": {"a": {"pd": 0.5}}},
        [],
        [],
        [_anchor("empty", None), _anchor("blank", "")],
    )
    assert result.anchors_term == pytest.approx(0.5)


def test_manual_anchor_only_blank_values_forces_neutral() -> None:
    """No scorer anchors and every manual value blank → neutral 1.0."""
    result = compute_market_pd(None, [], [], [_anchor("empty", None)])
    assert result.anchors_term == 1.0


def test_manual_anchor_pd_at_one_rejected() -> None:
    with pytest.raises(MarketScoringError, match=r"in \[0, 1\)"):
        compute_market_pd(None, [], [], [_anchor("k", 1.0)])


def test_manual_anchor_unparseable_value_rejected() -> None:
    with pytest.raises(MarketScoringError, match="not a valid float probability"):
        compute_market_pd(None, [], [], [_anchor("k", "high")])


def test_manual_anchor_key_collision_is_namespaced() -> None:
    """A manual key equal to a scorer key is namespaced, so both contribute.

    Scorer pd=0.3 under 'shared', manual pd=0.2 also named 'shared' →
    keys become 'shared' and 'manual:shared'; both fold in:
    1 − (0.7 × 0.8) = 0.44.
    """
    result = compute_market_pd(
        {"anchors": {"shared": {"pd": 0.3}}}, [], [], [_anchor("shared", 0.2)]
    )
    assert result.anchors_term == pytest.approx(0.44)
    keys = {c["subCategory"] for c in result.breakdown["anchors"]}
    assert keys == {"shared", "manual:shared"}


def test_manual_anchor_breakdown_marks_source() -> None:
    """The breakdown tags scorer vs. manual anchors via ``source``."""
    result = compute_market_pd(
        {"anchors": {"a": {"pd": 0.3}}},
        [],
        [],
        [_anchor("Bridging Risk", 0.2, notes="see report")],
    )
    by_sub = {c["subCategory"]: c for c in result.breakdown["anchors"]}
    assert by_sub["a"]["source"] == "scorer"
    assert by_sub["Bridging Risk"]["source"] == "manual"
    assert by_sub["Bridging Risk"]["weight"] == 1.0
    assert by_sub["Bridging Risk"]["rationale"] == "see report"


def test_manual_anchor_prefers_sub_category_as_key() -> None:
    """When set, ``sub_category`` is the display/breakdown key, not ``name``."""
    result = compute_market_pd(
        None, [], [], [_anchor("Bridging Risk", 0.2, sub_category="bridgingRisk")]
    )
    [row] = result.breakdown["anchors"]
    assert row["subCategory"] == "bridgingRisk"
