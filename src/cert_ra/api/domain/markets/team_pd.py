# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Team-aware market PD recomputation.

The persisted :class:`MarketScore` is computed by the scorer activity
against the *global* default weighting profile (``team_id IS NULL``). Any
surface that shows a market's PD to a member of a team that has its own
weighting profile must recompute it against that profile so the number is
consistent with the market detail page.

This module is the single source of that recomputation, shared by the
dashboard-card resolver (and available to any other read path). The
market detail controller has its own inline recomputation that predates
this helper; both apply the same precedence and calculator.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import desc, select

from cert_ra.api.domain.markets.assurance import (
    load_market_anchors,
    load_protocol_assurance,
)
from cert_ra.api.domain.weighting_profiles.resolver import (
    resolve_weighting_profile_entries,
)
from cert_ra.db.models import AutomatedMarketSnapshot
from cert_ra.metrics.market.scoring import MarketScoringError, compute_market_pd
from cert_ra.types import MarketSnapshotKind

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from cert_ra.db.models import MarketConfig

__all__ = ("compute_team_market_final_pd",)


async def compute_team_market_final_pd(
    session: AsyncSession,
    *,
    protocol: MarketConfig,
    chain_id: int,
    market_id_hex: str,
    team_id: UUID | None,
) -> Decimal | None:
    """Recompute one market's final PD using ``team_id``'s weighting profile.

    Resolution precedence (handled by ``resolve_weighting_profile_entries``):
    team+market → team+protocol → global+market → global+protocol. The
    ASSURANCE term unions shared (operator-published) rows with the team's
    own published rows, matching the market detail page.

    Args:
        session: Open async session.
        protocol: The operator-registered protocol row the market lives under.
        chain_id: Chain id of the specific market within the protocol.
        market_id_hex: On-chain id (hex) of the specific market.
        team_id: The viewer's current team. ``None`` returns ``None`` so
            callers fall back to the stored global ``MarketScore``.

    Returns:
        The team-weighted final PD, or ``None`` when it can't be recomputed
        (no team, no SCORE snapshot yet, empty score payload, or a scoring
        error) — callers fall back to the stored global value.
    """
    if team_id is None:
        return None
    snapshot = (
        await session.scalars(
            select(AutomatedMarketSnapshot)
            .where(
                AutomatedMarketSnapshot.market_config_id == protocol.id,
                AutomatedMarketSnapshot.chain_id == chain_id,
                AutomatedMarketSnapshot.market_id_hex == market_id_hex,
                AutomatedMarketSnapshot.kind == MarketSnapshotKind.SCORE,
            )
            .order_by(desc(AutomatedMarketSnapshot.created_at))
            .limit(1)
        )
    ).first()
    if snapshot is None or not isinstance(snapshot.score, dict) or not snapshot.score:
        return None
    profile_entries = await resolve_weighting_profile_entries(
        session,
        protocol=protocol.protocol,
        market_config_id=protocol.id,
        chain_id=chain_id,
        market_id_hex=market_id_hex,
        team_id=team_id,
    )
    assurance = await load_protocol_assurance(session, protocol, team_id)
    manual_anchors = await load_market_anchors(
        session,
        protocol,
        chain_id=chain_id,
        market_id_hex=market_id_hex,
        team_id=team_id,
    )
    try:
        breakdown = compute_market_pd(
            snapshot.score, profile_entries, assurance, manual_anchors
        )
    except MarketScoringError:
        return None
    return Decimal(repr(breakdown.final_pd))
