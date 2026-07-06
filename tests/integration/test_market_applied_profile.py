# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Applied weighting profile surfaced on the market show page.

``_resolve_pd_card`` returns both the PD card and the weighting profile
that shaped it, so the show page can name the active profile and badge
the overridden sub-categories. The applied profile follows the same
precedence path as the displayed PD:

* viewer with a team → the team's resolved profile,
* no team (stored MarketScore path) → the global default in effect,
* no matching profile → ``None`` (every weight defaulted to 1.0).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cert_ra.api.domain.markets.controllers import _resolve_pd_card
from cert_ra.db.models import (
    AutomatedMarketSnapshot,
    MarketConfig,
    MarketScore,
    Team,
    User,
    WeightingProfile,
    WeightingProfileEntry,
)
from cert_ra.types import (
    MarketSnapshotKind,
    WeightingProfileEntryCategory,
    WeightingProfileScope,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

_CHAIN_ID = 1
_MARKET_HEX = "0x" + "00" * 32
_LABEL = "Aave USDC"


async def _make_market(
    session: AsyncSession, suffix: str
) -> tuple[MarketConfig, AutomatedMarketSnapshot, MarketScore]:
    """Protocol row + SCORE snapshot + stored global MarketScore."""
    user = User(email=f"ap-{suffix}@example.com", is_active=True, is_verified=True)
    session.add(user)
    await session.flush()
    market = MarketConfig(
        protocol=f"aave-{suffix}",
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
        score={"anchors": {"k": {"pd": 0.5}}},
    )
    session.add(snapshot)
    await session.flush()
    score = MarketScore(
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
    session.add(score)
    await session.flush()
    return market, snapshot, score


async def _add_profile(
    session: AsyncSession,
    *,
    market_id: UUID,
    team_id: UUID | None,
    name: str,
    weight: str,
) -> None:
    """Market-scoped profile (team or global) overriding anchor 'k'."""
    owner = User(email=f"owner-{name}@example.com", is_active=True)
    session.add(owner)
    await session.flush()
    profile = WeightingProfile(
        team_id=team_id,
        name=name,
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


async def test_team_profile_is_reported(session: AsyncSession) -> None:
    """Viewer with a team sees their team profile named + override count."""
    market, snapshot, score = await _make_market(session, "team")
    team = Team(name="Risk Team", slug="risk-team")
    session.add(team)
    await session.flush()
    await _add_profile(
        session, market_id=market.id, team_id=team.id, name="Custom", weight="1.5"
    )
    await session.commit()

    pd, applied = await _resolve_pd_card(
        session,
        market=market,
        chain_id=_CHAIN_ID,
        market_id_hex=_MARKET_HEX,
        viewer_team_id=team.id,
        latest_score=score,
        latest_score_snapshot=snapshot,
        assurance_metrics=[],
    )
    assert pd is not None
    assert float(pd.final_pd) == pytest.approx(0.75)  # team weight applied
    assert applied is not None
    assert applied.name == "Custom"
    assert applied.is_global is False
    assert applied.team_name == "Risk Team"
    assert applied.scope == WeightingProfileScope.MARKET.value
    assert applied.override_count == 1


async def test_global_default_reported_without_team(session: AsyncSession) -> None:
    """No team → stored-score path names the global default profile."""
    market, snapshot, score = await _make_market(session, "global")
    await _add_profile(
        session, market_id=market.id, team_id=None, name="House", weight="2"
    )
    await session.commit()

    pd, applied = await _resolve_pd_card(
        session,
        market=market,
        chain_id=_CHAIN_ID,
        market_id_hex=_MARKET_HEX,
        viewer_team_id=None,
        latest_score=score,
        latest_score_snapshot=snapshot,
        assurance_metrics=[],
    )
    assert pd is not None
    assert applied is not None
    assert applied.name == "House"
    assert applied.is_global is True
    assert applied.team_name is None


async def test_no_profile_reports_none(session: AsyncSession) -> None:
    """No matching profile → applied profile is None (defaults applied)."""
    market, snapshot, score = await _make_market(session, "none")
    await session.commit()

    pd, applied = await _resolve_pd_card(
        session,
        market=market,
        chain_id=_CHAIN_ID,
        market_id_hex=_MARKET_HEX,
        viewer_team_id=None,
        latest_score=score,
        latest_score_snapshot=snapshot,
        assurance_metrics=[],
    )
    assert pd is not None
    assert applied is None
