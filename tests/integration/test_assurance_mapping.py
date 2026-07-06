# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Operator-set protocol→ProtocolType mapping for ASSURANCE lookups.

``MarketConfig.protocol`` is a lowercase yarn slug; ASSURANCE manual
metrics are keyed by the ``ProtocolType`` enum. ``MarketConfig.
assurance_protocol`` bridges the two (or is ``None`` for "no assurance").
``load_protocol_assurance`` reads that mapping — never comparing a slug
against the enum, which would error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cert_ra.api.domain.markets.assurance import (
    assurance_protocol_for,
    load_protocol_assurance,
)
from cert_ra.db.models import ManualMetric, MarketConfig, Team, User
from cert_ra.types import MetricCategory, ProtocolType

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio


async def _market(
    session: AsyncSession,
    *,
    slug: str,
    assurance_protocol: ProtocolType | None,
) -> UUID:
    user = User(email=f"mc-{slug}@example.com", is_active=True, is_verified=True)
    session.add(user)
    await session.flush()
    market = MarketConfig(
        protocol=slug,
        assurance_protocol=assurance_protocol,
        enabled=True,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(market)
    await session.flush()
    return market.id


async def _assurance_metric(
    session: AsyncSession,
    *,
    protocol: ProtocolType,
    sub_category: str,
    team_id: UUID | None,
) -> None:
    user = User(email=f"author-{sub_category}-{team_id}@example.com", is_active=True)
    session.add(user)
    await session.flush()
    session.add(
        ManualMetric(
            name=f"Metric {sub_category}",
            desc="x",
            category=MetricCategory.ASSURANCE,
            protocol=protocol,
            sub_category=sub_category,
            value="1.05",
            is_published=True,
            team_id=team_id,
            created_by=user.id,
            updated_by=user.id,
        )
    )


async def test_assurance_protocol_for_returns_mapping(
    session: AsyncSession,
) -> None:
    market_id = await _market(
        session, slug="aave", assurance_protocol=ProtocolType.AAVE_V3
    )
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None
    assert assurance_protocol_for(market) == ProtocolType.AAVE_V3


async def test_loads_assurance_by_mapped_protocol(session: AsyncSession) -> None:
    """A mapped market loads ASSURANCE metrics keyed by the ProtocolType."""
    market_id = await _market(
        session, slug="aave", assurance_protocol=ProtocolType.AAVE_V3
    )
    await _assurance_metric(
        session, protocol=ProtocolType.AAVE_V3, sub_category="audit", team_id=None
    )
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None
    rows = await load_protocol_assurance(session, market, None)
    assert [r.sub_category for r in rows] == ["audit"]


async def test_unmapped_protocol_returns_empty_without_error(
    session: AsyncSession,
) -> None:
    """A lowercase slug with no mapping returns [] — no invalid-enum error."""
    market_id = await _market(session, slug="aave", assurance_protocol=None)
    # An ASSURANCE row exists for AAVE_V3, but this market isn't mapped to it.
    await _assurance_metric(
        session, protocol=ProtocolType.AAVE_V3, sub_category="audit", team_id=None
    )
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None
    rows = await load_protocol_assurance(session, market, None)
    assert rows == []


async def test_team_assurance_unioned_with_shared(session: AsyncSession) -> None:
    """With a team, shared + that team's own ASSURANCE rows are returned."""
    market_id = await _market(
        session, slug="aave", assurance_protocol=ProtocolType.AAVE_V3
    )
    team = Team(name="T", slug="assurance-team")
    session.add(team)
    await session.flush()
    team_id = team.id
    await _assurance_metric(
        session, protocol=ProtocolType.AAVE_V3, sub_category="shared", team_id=None
    )
    await _assurance_metric(
        session, protocol=ProtocolType.AAVE_V3, sub_category="team", team_id=team_id
    )
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None

    shared_only = await load_protocol_assurance(session, market, None)
    assert {r.sub_category for r in shared_only} == {"shared"}

    with_team = await load_protocol_assurance(session, market, team_id)
    assert {r.sub_category for r in with_team} == {"shared", "team"}
