# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Persistence of AutomatedMarketSnapshot rows against a real database.

Guards the ``ck_amk_snapshot_score_for_score_kind`` check constraint,
which requires ``score IS NULL`` for ``kind = 'COLLECT'`` rows. The
``score`` column is JSONB; without ``none_as_null=True`` SQLAlchemy
writes a Python ``None`` as a JSONB ``'null'`` scalar (which is *not*
``IS NULL``), so every COLLECT insert used to fail with an
``IntegrityError`` and no collector snapshot was ever stored. A mocked
session can't catch this — it only surfaces against Postgres.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cert_ra.db.models import AutomatedMarketSnapshot, MarketConfig, User
from cert_ra.types import MarketSnapshotKind

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

_CHAIN_ID = 1
_MARKET_HEX = "0x" + "00" * 32


async def _make_protocol(session: AsyncSession) -> MarketConfig:
    user = User(email="snap@example.com", is_active=True, is_verified=True)
    session.add(user)
    await session.flush()
    market = MarketConfig(
        protocol="aave-snap",
        enabled=True,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(market)
    await session.flush()
    return market


async def test_collect_snapshot_with_null_score_persists(session: AsyncSession) -> None:
    """A COLLECT row with ``score=None`` commits and round-trips as SQL NULL."""
    market = await _make_protocol(session)
    session.add(
        AutomatedMarketSnapshot(
            market_config_id=market.id,
            chain_id=_CHAIN_ID,
            market_id_hex=_MARKET_HEX,
            label="AaveV3Ethereum",
            kind=MarketSnapshotKind.COLLECT,
            anchors={"marketSolvency": {"totalSupplied": "1"}},
            modifiers={"collateralDependencyRobustness": {"reserveFactor": "0.1"}},
            score=None,
        )
    )
    # Would raise IntegrityError (CheckViolation) before the
    # ``none_as_null=True`` fix on the score column.
    await session.commit()

    stored = (
        await session.scalars(
            select(AutomatedMarketSnapshot).where(
                AutomatedMarketSnapshot.market_config_id == market.id,
                AutomatedMarketSnapshot.kind == MarketSnapshotKind.COLLECT,
            )
        )
    ).one()
    assert stored.score is None
    assert stored.anchors == {"marketSolvency": {"totalSupplied": "1"}}
    assert stored.modifiers == {
        "collateralDependencyRobustness": {"reserveFactor": "0.1"}
    }
