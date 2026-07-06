# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Pure-function validators for manual-metric create payloads.

``validate_anchor_probability`` and ``_validate_market_pin`` mirror the DB
constraints and the PD calculator's contract so the controller can surface
clean 400s instead of IntegrityErrors / scoring failures.
"""

from __future__ import annotations

import pytest

from cert_ra.api.domain.manual_metrics.services import (
    _validate_market_pin,
    validate_anchor_probability,
)
from cert_ra.types import MetricCategory, ProtocolType

# ---------------------------------------------------------------------------
# validate_anchor_probability
# ---------------------------------------------------------------------------


def test_anchor_probability_accepts_value_in_range() -> None:
    # No raise.
    validate_anchor_probability(MetricCategory.ANCHORS, "0.2")
    validate_anchor_probability(MetricCategory.ANCHORS, "0")


def test_anchor_probability_blank_value_allowed() -> None:
    """A blank value is neutral (the row is a draft until filled in)."""
    validate_anchor_probability(MetricCategory.ANCHORS, None)
    validate_anchor_probability(MetricCategory.ANCHORS, "")


def test_anchor_probability_rejects_one_and_above() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        validate_anchor_probability(MetricCategory.ANCHORS, "1.0")
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        validate_anchor_probability(MetricCategory.ANCHORS, "1.5")


def test_anchor_probability_rejects_negative() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        validate_anchor_probability(MetricCategory.ANCHORS, "-0.1")


def test_anchor_probability_rejects_non_numeric() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        validate_anchor_probability(MetricCategory.ANCHORS, "high")


def test_anchor_probability_ignores_non_anchor_category() -> None:
    """ASSURANCE keeps its own value semantics — not range-checked here."""
    validate_anchor_probability(MetricCategory.ASSURANCE, "1.1")
    validate_anchor_probability(MetricCategory.ASSURANCE, "passing")
    validate_anchor_probability(None, "anything")


# ---------------------------------------------------------------------------
# _validate_market_pin
# ---------------------------------------------------------------------------


def _base_anchor() -> dict:
    return {"protocol": ProtocolType.AAVE_V3, "category": MetricCategory.ANCHORS}


def test_pin_unset_is_allowed() -> None:
    _validate_market_pin(_base_anchor())


def test_pin_both_set_on_protocol_anchor_allowed() -> None:
    data = _base_anchor() | {"market_chain_id": 8453, "market_id_hex": "0xabc"}
    _validate_market_pin(data)


def test_pin_half_set_rejected() -> None:
    data = _base_anchor() | {"market_chain_id": 8453, "market_id_hex": None}
    with pytest.raises(ValueError, match="set together"):
        _validate_market_pin(data)


def test_pin_without_protocol_rejected() -> None:
    data = {
        "protocol": None,
        "token": "USDC",
        "category": MetricCategory.ANCHORS,
        "market_chain_id": 8453,
        "market_id_hex": "0xabc",
    }
    with pytest.raises(ValueError, match="protocol-scoped"):
        _validate_market_pin(data)


def test_pin_on_non_anchor_category_rejected() -> None:
    data = {
        "protocol": ProtocolType.AAVE_V3,
        "category": MetricCategory.ASSURANCE,
        "market_chain_id": 8453,
        "market_id_hex": "0xabc",
    }
    with pytest.raises(ValueError, match="ANCHORS"):
        _validate_market_pin(data)
