# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Loader for manual ANCHORS metrics that feed a market's anchors term.

``load_market_anchors`` returns the published, non-deleted ANCHORS rows
that apply to one market: those whose ``protocol`` matches the market's
``assurance_protocol`` mapping and that are either unpinned (apply to
every market of the protocol) or pinned to exactly this market. It also
unions shared rows with a team's own rows, and excludes soft-deleted rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cert_ra.api.domain.markets.assurance import (
    load_market_anchors,
    load_protocol_assurance,
)
from cert_ra.db.models import ManualMetric, MarketConfig, Team, User
from cert_ra.types import MetricCategory, ProtocolType

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

_CHAIN = 8453
_HEX = "0x" + "ab" * 32
_OTHER_HEX = "0x" + "cd" * 32


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


async def _anchor(
    session: AsyncSession,
    *,
    protocol: ProtocolType,
    name: str,
    value: str | None = "0.2",
    team_id: UUID | None = None,
    is_published: bool = True,
    deleted: bool = False,
    market_chain_id: int | None = None,
    market_id_hex: str | None = None,
) -> None:
    user = User(email=f"author-{name}-{team_id}@example.com", is_active=True)
    session.add(user)
    await session.flush()
    session.add(
        ManualMetric(
            name=name,
            desc="x",
            category=MetricCategory.ANCHORS,
            protocol=protocol,
            value=value,
            is_published=is_published,
            deleted=deleted,
            market_chain_id=market_chain_id,
            market_id_hex=market_id_hex,
            team_id=team_id,
            created_by=user.id,
            updated_by=user.id,
        )
    )


async def test_unpinned_anchor_applies_to_any_market(session: AsyncSession) -> None:
    market_id = await _market(
        session, slug="aave", assurance_protocol=ProtocolType.AAVE_V3
    )
    await _anchor(session, protocol=ProtocolType.AAVE_V3, name="Bridging Risk")
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None
    rows = await load_market_anchors(
        session, market, chain_id=_CHAIN, market_id_hex=_HEX, team_id=None
    )
    assert [r.name for r in rows] == ["Bridging Risk"]


async def test_pinned_anchor_matches_only_its_market(session: AsyncSession) -> None:
    market_id = await _market(
        session, slug="aave", assurance_protocol=ProtocolType.AAVE_V3
    )
    await _anchor(
        session,
        protocol=ProtocolType.AAVE_V3,
        name="Pinned",
        market_chain_id=_CHAIN,
        market_id_hex=_HEX,
    )
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None

    matching = await load_market_anchors(
        session, market, chain_id=_CHAIN, market_id_hex=_HEX, team_id=None
    )
    assert [r.name for r in matching] == ["Pinned"]

    # A different market of the same protocol must NOT see the pinned row.
    other = await load_market_anchors(
        session, market, chain_id=_CHAIN, market_id_hex=_OTHER_HEX, team_id=None
    )
    assert other == []


async def test_deleted_anchor_excluded(session: AsyncSession) -> None:
    market_id = await _market(
        session, slug="aave", assurance_protocol=ProtocolType.AAVE_V3
    )
    await _anchor(session, protocol=ProtocolType.AAVE_V3, name="Live")
    await _anchor(session, protocol=ProtocolType.AAVE_V3, name="Retired", deleted=True)
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None
    rows = await load_market_anchors(
        session, market, chain_id=_CHAIN, market_id_hex=_HEX, team_id=None
    )
    assert [r.name for r in rows] == ["Live"]


async def test_unpublished_anchor_excluded(session: AsyncSession) -> None:
    market_id = await _market(
        session, slug="aave", assurance_protocol=ProtocolType.AAVE_V3
    )
    await _anchor(
        session, protocol=ProtocolType.AAVE_V3, name="Draft", is_published=False
    )
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None
    rows = await load_market_anchors(
        session, market, chain_id=_CHAIN, market_id_hex=_HEX, team_id=None
    )
    assert rows == []


async def test_unmapped_protocol_returns_empty(session: AsyncSession) -> None:
    market_id = await _market(session, slug="aave", assurance_protocol=None)
    await _anchor(session, protocol=ProtocolType.AAVE_V3, name="Orphan")
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None
    rows = await load_market_anchors(
        session, market, chain_id=_CHAIN, market_id_hex=_HEX, team_id=None
    )
    assert rows == []


async def test_team_anchors_unioned_with_shared(session: AsyncSession) -> None:
    market_id = await _market(
        session, slug="aave", assurance_protocol=ProtocolType.AAVE_V3
    )
    team = Team(name="T", slug="anchor-team")
    session.add(team)
    await session.flush()
    team_id = team.id
    await _anchor(session, protocol=ProtocolType.AAVE_V3, name="shared", team_id=None)
    await _anchor(session, protocol=ProtocolType.AAVE_V3, name="team", team_id=team_id)
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None

    shared_only = await load_market_anchors(
        session, market, chain_id=_CHAIN, market_id_hex=_HEX, team_id=None
    )
    assert {r.name for r in shared_only} == {"shared"}

    with_team = await load_market_anchors(
        session, market, chain_id=_CHAIN, market_id_hex=_HEX, team_id=team_id
    )
    assert {r.name for r in with_team} == {"shared", "team"}


async def test_assurance_loader_excludes_deleted(session: AsyncSession) -> None:
    """The deleted filter also applies to the ASSURANCE loader."""
    market_id = await _market(
        session, slug="aave", assurance_protocol=ProtocolType.AAVE_V3
    )
    user = User(email="assurance-author@example.com", is_active=True)
    session.add(user)
    await session.flush()
    session.add(
        ManualMetric(
            name="Retired Assurance",
            desc="x",
            category=MetricCategory.ASSURANCE,
            protocol=ProtocolType.AAVE_V3,
            value="1.05",
            is_published=True,
            deleted=True,
            created_by=user.id,
            updated_by=user.id,
        )
    )
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None
    rows = await load_protocol_assurance(session, market, None)
    assert rows == []
