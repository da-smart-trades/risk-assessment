# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unit tests for the manual-metric seeders."""

from __future__ import annotations

from uuid import uuid4

from cert_ra.db.seed_manual_metrics import _build_rows
from cert_ra.db.seed_manual_metrics_governance import (
    _build_rows as _build_governance_rows,
)
from cert_ra.types import ChainType, MetricCategory, ProtocolType


def test_build_rows_publishes_seeded_metrics() -> None:
    """Seeded rows must be published so the PROTOCOL_SCORE summary is favoritable.

    Favorites reject draft (unpublished) metrics, so a seeded-but-unpublished
    PROTOCOL_SCORE summary makes the protocol star render yet fail to pin.
    """
    author_id = uuid4()
    metrics_in = [
        {
            "category": "PROTOCOL_SCORE",
            "sub_category": "SUMMARY",
            "name": "Probability of Default",
            "value": "0.03",
        }
    ]

    rows = _build_rows(metrics_in, ProtocolType.AAVE_V3, author_id)

    assert len(rows) == 1
    row = rows[0]
    assert row.is_published is True
    assert row.category is MetricCategory.PROTOCOL_SCORE
    assert row.protocol is ProtocolType.AAVE_V3
    assert row.team_id is None  # shared / operator-published scope


def test_build_governance_rows_publishes_seeded_metrics() -> None:
    """Seeded governance rows must be published or they surface nowhere.

    The chain dashboard, markets assurance, and weighting profiles all filter
    on ``is_published=True``, so an unpublished governance row exists in the
    table but never displays — the regression that hid manual governance
    metrics on prod.
    """
    author_id = uuid4()
    rows_in = [
        {
            "name": "Slashing Behavior",
            "desc": "Slashing behavior in the network",
            "category": "GOVERNANCE",
            "value": "True",
        }
    ]

    rows = _build_governance_rows(rows_in, ChainType.ETHEREUM, author_id)

    assert len(rows) == 1
    row = rows[0]
    assert row.is_published is True
    assert row.category is MetricCategory.GOVERNANCE
    assert row.chain is ChainType.ETHEREUM
    assert row.team_id is None  # shared / operator-published scope
