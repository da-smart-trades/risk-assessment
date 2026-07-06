# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Team-weighted market PD on dashboard cards.

A market's persisted ``MarketScore`` uses the global default weights. When
a viewer has a team with its own weighting profile, dashboard cards (and
the shared ``compute_team_market_final_pd`` helper) must reflect the
team's weights — matching the market detail page.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import select

from cert_ra.api.domain.favorites.resolver import resolve_favorites
from cert_ra.api.domain.markets.team_pd import compute_team_market_final_pd
from cert_ra.db.models import (
    AutomatedMarketSnapshot,
    Dashboard,
    MarketConfig,
    MarketScore,
    Team,
    User,
    UserFavoriteMetric,
    WeightingProfile,
    WeightingProfileEntry,
)
from cert_ra.types import (
    MarketSnapshotKind,
    WeightingProfileEntryCategory,
    WeightingProfileScope,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

_ANCHOR_PD = 0.5  # global anchors_term = final_pd = 0.5 (no controls/assurance)
_CHAIN_ID = 1
_MARKET_HEX = "0x" + "00" * 32
_LABEL = "Aave USDC"


async def _make_market(session: AsyncSession, suffix: str) -> UUID:
    """Create a protocol row + SCORE snapshot + global MarketScore (final_pd=0.5).

    The market identity ``(chain_id, market_id_hex, label)`` now lives on
    the snapshot / score / favorite rows; ``market_config`` is just the
    protocol header.
    """
    user = User(email=f"wp-{suffix}@example.com", is_active=True, is_verified=True)
    session.add(user)
    await session.flush()
    protocol_slug = f"aave-{suffix}"
    market = MarketConfig(
        protocol=protocol_slug,
        enabled=True,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(market)
    await session.flush()
    snapshot = AutomatedMarketSnapshot(
        market_config_id=market.id,
        chain_id=_CHAIN_ID,
        market_id_hex=_MARKET_HEX,
        label=_LABEL,
        kind=MarketSnapshotKind.SCORE,
        anchors={},
        modifiers={},
        score={"anchors": {"k": {"pd": _ANCHOR_PD}}},
    )
    session.add(snapshot)
    await session.flush()
    session.add(
        MarketScore(
            market_config_id=market.id,
            chain_id=_CHAIN_ID,
            market_id_hex=_MARKET_HEX,
            label=_LABEL,
            source_amk_snapshot_id=snapshot.id,
            final_pd=Decimal("0.5"),
            anchors_term=Decimal("0.5"),
            control_term=Decimal(1),
            assurance_term=Decimal(1),
            breakdown={},
        )
    )
    await session.flush()
    return market.id


async def _add_team_profile(
    session: AsyncSession, *, market_id: UUID, weight: str
) -> UUID:
    """Team weighting profile scoped to the market, overriding anchor 'k'."""
    owner = User(email=f"owner-{market_id}@example.com", is_active=True)
    session.add(owner)
    team = Team(name=f"Team {market_id}", slug=f"team-{market_id}")
    session.add_all([owner, team])
    await session.flush()
    profile = WeightingProfile(
        team_id=team.id,
        name="Custom",
        scope=WeightingProfileScope.MARKET,
        target_market_config_id=market_id,
        target_chain_id=_CHAIN_ID,
        target_market_id_hex=_MARKET_HEX,
        target_label=_LABEL,
        created_by=owner.id,
        updated_by=owner.id,
    )
    session.add(profile)
    await session.flush()
    session.add(
        WeightingProfileEntry(
            weighting_profile_id=profile.id,
            category=WeightingProfileEntryCategory.ANCHOR,
            sub_category="k",
            weight=Decimal(weight),
        )
    )
    await session.flush()
    return team.id


async def test_team_weight_changes_final_pd(session: AsyncSession) -> None:
    """A team weight of 1.5 lifts anchor pd 0.5 → final 0.75 (global is 0.5)."""
    market_id = await _make_market(session, "weighted")
    team_id = await _add_team_profile(session, market_id=market_id, weight="1.5")
    await session.commit()

    market = await session.get(MarketConfig, market_id)
    assert market is not None
    team_pd = await compute_team_market_final_pd(
        session,
        protocol=market,
        chain_id=_CHAIN_ID,
        market_id_hex=_MARKET_HEX,
        team_id=team_id,
    )
    assert team_pd is not None
    assert float(team_pd) == pytest.approx(0.75)


async def test_no_team_returns_none(session: AsyncSession) -> None:
    """No team → helper returns None so callers fall back to the global score."""
    market_id = await _make_market(session, "noteam")
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None
    assert (
        await compute_team_market_final_pd(
            session,
            protocol=market,
            chain_id=_CHAIN_ID,
            market_id_hex=_MARKET_HEX,
            team_id=None,
        )
        is None
    )


async def test_team_without_profile_uses_unit_weights(
    session: AsyncSession,
) -> None:
    """A team with no profile recomputes with weight 1.0 → equals global 0.5."""
    market_id = await _make_market(session, "noprofile")
    team = Team(name="Bare", slug="bare-team")
    session.add(team)
    await session.flush()
    team_id = team.id
    await session.commit()
    market = await session.get(MarketConfig, market_id)
    assert market is not None
    team_pd = await compute_team_market_final_pd(
        session,
        protocol=market,
        chain_id=_CHAIN_ID,
        market_id_hex=_MARKET_HEX,
        team_id=team_id,
    )
    assert team_pd is not None
    assert float(team_pd) == pytest.approx(0.5)


async def test_dashboard_card_reflects_team_weight(session: AsyncSession) -> None:
    """resolve_favorites: market card shows team-weighted PD vs global."""
    market_id = await _make_market(session, "card")
    team_id = await _add_team_profile(session, market_id=market_id, weight="1.5")
    dash_owner = User(email="dash-owner@example.com", is_active=True)
    session.add(dash_owner)
    await session.flush()
    dashboard = Dashboard(owner_id=dash_owner.id, name="Home", is_default=True)
    session.add(dashboard)
    await session.flush()
    session.add(
        UserFavoriteMetric(
            dashboard_id=dashboard.id,
            market_config_id=market_id,
            favorite_chain_id=_CHAIN_ID,
            favorite_market_id_hex=_MARKET_HEX,
            favorite_label=_LABEL,
            position=0,
        )
    )
    dashboard_id = dashboard.id
    await session.commit()

    favorites = list(
        (
            await session.execute(
                select(UserFavoriteMetric).where(
                    UserFavoriteMetric.dashboard_id == dashboard_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(favorites) == 1

    team_cards = await resolve_favorites(session, favorites, team_id=team_id)
    global_cards = await resolve_favorites(session, favorites, team_id=None)
    assert team_cards[0].value == "75.00%"
    assert global_cards[0].value == "50.00%"
