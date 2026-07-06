# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for pure decentralization math helpers."""

from __future__ import annotations

import math

import pytest

from cert_ra.metrics.decentralization.calculations import (
    group_by_entity,
    hhi,
    nakamoto_coefficient,
    renyi_entropy,
    shapley_top_values,
)

# ---------------------------------------------------------------------------
# Nakamoto coefficient
# ---------------------------------------------------------------------------


def test_nakamoto_coefficient_empty() -> None:
    assert nakamoto_coefficient([]) == 0


def test_nakamoto_coefficient_single_validator() -> None:
    # One validator controls 100% — one entity passes any threshold.
    assert nakamoto_coefficient([100.0]) == 1
    assert nakamoto_coefficient([100.0], threshold=0.666) == 1


def test_nakamoto_coefficient_equal_stakes() -> None:
    # 10 equal validators; 4 of them pass 33.3% (40% > 33.3%).
    assert nakamoto_coefficient([1.0] * 10, threshold=0.333) == 4
    # 7 of them pass 66.6% (70% > 66.6%).
    assert nakamoto_coefficient([1.0] * 10, threshold=0.666) == 7


def test_nakamoto_coefficient_skewed_distribution() -> None:
    # 50%, 30%, 10%, 10% — top validator already holds 50% > 33.3%.
    assert nakamoto_coefficient([50, 30, 10, 10], threshold=0.333) == 1
    # Two validators hold 80% > 66.6%.
    assert nakamoto_coefficient([50, 30, 10, 10], threshold=0.666) == 2


def test_nakamoto_coefficient_sort_independent() -> None:
    # Same stakes in different order yield the same coefficient.
    assert nakamoto_coefficient([10, 50, 30, 10]) == nakamoto_coefficient(
        [50, 30, 10, 10]
    )


# ---------------------------------------------------------------------------
# Herfindahl-Hirschman Index
# ---------------------------------------------------------------------------


def test_hhi_empty_returns_zero() -> None:
    assert hhi([]) == 0.0


def test_hhi_zero_stakes_returns_zero() -> None:
    assert hhi([0.0, 0.0]) == 0.0


def test_hhi_monopoly_equals_one() -> None:
    assert hhi([1.0]) == 1.0
    assert hhi([42.0]) == 1.0


def test_hhi_equal_distribution() -> None:
    # N equal players → HHI = 1/N.
    assert hhi([1.0, 1.0, 1.0, 1.0]) == pytest.approx(0.25)
    assert hhi([1.0] * 10) == pytest.approx(0.1)


def test_hhi_increases_with_concentration() -> None:
    # More concentrated stake distributions produce a larger HHI.
    spread = hhi([25, 25, 25, 25])
    concentrated = hhi([70, 10, 10, 10])
    assert concentrated > spread


# ---------------------------------------------------------------------------
# Renyi entropy
# ---------------------------------------------------------------------------


def test_renyi_empty_returns_zero() -> None:
    assert renyi_entropy([], alpha=2) == 0.0


def test_renyi_all_zero_returns_zero() -> None:
    assert renyi_entropy([0.0, 0.0], alpha=2) == 0.0


def test_renyi_alpha_0_counts_positive_entities() -> None:
    # Alpha=0 ignores weights entirely — entropy is log(count_non_zero).
    assert renyi_entropy([1, 2, 3, 4], alpha=0) == pytest.approx(math.log(4))
    assert renyi_entropy([1, 0, 3, 4], alpha=0) == pytest.approx(math.log(3))


def test_renyi_alpha_1_matches_shannon() -> None:
    # Equal distribution: Shannon entropy = log(N).
    assert renyi_entropy([1.0] * 4, alpha=1) == pytest.approx(math.log(4))


def test_renyi_alpha_inf_is_min_entropy() -> None:
    # -log(max probability). For 50/50: -log(0.5) = ln(2).
    assert renyi_entropy([1.0, 1.0], alpha=math.inf) == pytest.approx(math.log(2))


def test_renyi_alpha_2_general_formula() -> None:
    # 4 equal stakes: sum(p^2) = 4 * (0.25)^2 = 0.25 → -log(0.25) = log(4).
    assert renyi_entropy([1.0] * 4, alpha=2) == pytest.approx(math.log(4))


def test_renyi_concentrated_has_lower_entropy() -> None:
    spread = renyi_entropy([1.0] * 10, alpha=2)
    concentrated = renyi_entropy([100, 1, 1, 1, 1], alpha=2)
    assert concentrated < spread


# ---------------------------------------------------------------------------
# Shapley top values
# ---------------------------------------------------------------------------


def test_shapley_empty_returns_empty() -> None:
    assert shapley_top_values([]) == []


def test_shapley_values_sum_to_one() -> None:
    result = shapley_top_values([100, 50, 30, 20, 10])
    assert sum(result) == pytest.approx(1.0)


def test_shapley_returns_up_to_top_n() -> None:
    result = shapley_top_values([10, 20, 30, 40, 50], top_n=3)
    assert len(result) == 3


def test_shapley_handles_fewer_than_top_n() -> None:
    # Only 2 validators but top_n=3 → list has 2 entries.
    result = shapley_top_values([60, 40], top_n=3)
    assert len(result) == 2


def test_shapley_top_value_credits_pivotal_validator() -> None:
    # Top validator alone already exceeds 33.3% threshold → pivotal.
    # With stake sort [50, 30, 10, 10], cumulative=0 then +50 → past threshold.
    result = shapley_top_values([50, 30, 10, 10], threshold=0.333)
    # After normalization the pivotal validator should dominate.
    assert result[0] > result[1]


def test_shapley_all_zero_stakes() -> None:
    # All zero stakes — pivot never fires; list is zeros (post-normalization).
    result = shapley_top_values([0.0, 0.0, 0.0])
    assert all(v == 0.0 for v in result)


# ---------------------------------------------------------------------------
# group_by_entity
# ---------------------------------------------------------------------------


def test_group_by_entity_collapses_labeled_validators() -> None:
    stakes = [("v1", 32.0), ("v2", 32.0), ("v3", 32.0)]
    mapping = {"v1": "lido", "v2": "lido", "v3": "coinbase"}

    grouped, coverage = group_by_entity(stakes, mapping)
    grouped_dict = dict(grouped)

    assert grouped_dict["lido"] == pytest.approx(64.0)
    assert grouped_dict["coinbase"] == pytest.approx(32.0)
    assert coverage == pytest.approx(1.0)


def test_group_by_entity_keeps_unmapped_as_solos() -> None:
    stakes = [("v1", 32.0), ("v2", 32.0)]
    mapping = {"v1": "lido"}

    grouped, coverage = group_by_entity(stakes, mapping)
    grouped_dict = dict(grouped)

    assert grouped_dict["lido"] == pytest.approx(32.0)
    # v2 is preserved as its own entity, not merged into "lido".
    assert any(k.startswith("__unmapped__:") for k in grouped_dict)
    assert coverage == pytest.approx(0.5)


def test_group_by_entity_empty_input() -> None:
    grouped, coverage = group_by_entity([], {})
    assert grouped == []
    assert coverage == 0.0
