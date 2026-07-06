# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unit tests for the value-source registry helpers.

The full DB-backed read paths are exercised by integration tests; here we
focus on the synchronous pieces — the registry dispatch and the JSONB
extractor used by the market-anchor / market-control sources.
"""

from __future__ import annotations

import pytest

from cert_ra.alerts._value_sources import (
    MarketAnchorValueSource,
    MarketControlValueSource,
    MetricValueSource,
    lookup_metric_source,
    lookup_value_source,
)
from cert_ra.types import AlertTargetKind, MetricType


def test_registry_returns_metric_source_for_metric_kind() -> None:
    src = lookup_value_source(AlertTargetKind.METRIC)
    assert isinstance(src, MetricValueSource)


def test_registry_returns_specialised_sources_for_market_kinds() -> None:
    assert isinstance(
        lookup_value_source(AlertTargetKind.MARKET_ANCHOR), MarketAnchorValueSource
    )
    assert isinstance(
        lookup_value_source(AlertTargetKind.MARKET_CONTROL), MarketControlValueSource
    )


def test_lookup_metric_source_known_metric_returns_entry() -> None:
    source = lookup_metric_source(MetricType.GAS_PRICE)
    assert source is not None
    assert source.table == "throughput"
    assert source.column == "gas_price"


def test_lookup_metric_source_unknown_returns_none() -> None:
    # Metrics like UPGRADE_TRANSPARENCY are not in the alerts registry — they
    # have no automated source table.
    source = lookup_metric_source(MetricType.UPGRADE_TRANSPARENCY)
    assert source is None


@pytest.mark.parametrize(
    ("score", "sub_category", "expected"),
    [
        ({"anchors": {"liquidity": {"pd": 0.42}}}, "liquidity", 0.42),
        ({"anchors": {"liquidity": {"pd": "0.42"}}}, "liquidity", None),  # string drops
        ({"anchors": {"liquidity": {"pd": True}}}, "liquidity", None),  # bool drops
        ({"anchors": {}}, "liquidity", None),
        ({}, "liquidity", None),
        (None, "liquidity", None),
        ({"anchors": {"other": {"pd": 0.5}}}, "liquidity", None),
    ],
)
def test_anchor_extractor_pulls_pd_from_jsonb(
    score: dict | None, sub_category: str, expected: float | None
) -> None:
    source = MarketAnchorValueSource()
    assert source._extract(score, sub_category) == expected  # noqa: SLF001


@pytest.mark.parametrize(
    ("score", "sub_category", "expected"),
    [
        (
            {"controlModifiers": {"oracle": {"multiplier": 0.9}}},
            "oracle",
            0.9,
        ),
        ({"controlModifiers": {}}, "oracle", None),
        ({"anchors": {"oracle": {"multiplier": 0.9}}}, "oracle", None),  # wrong block
    ],
)
def test_control_extractor_pulls_multiplier_from_jsonb(
    score: dict | None, sub_category: str, expected: float | None
) -> None:
    source = MarketControlValueSource()
    assert source._extract(score, sub_category) == expected  # noqa: SLF001
