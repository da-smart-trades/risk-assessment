# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Resolve the applicable weighting profile for a (market, team) pair.

The PD calculator is parameterised by a flat list of
``WeightingProfileEntry`` rows, one per ``(category, sub_category)``
override. This module decides *which* profile's entries to use given
the precedence rules in the PRD:

1. team profile scoped to the specific market
2. team profile scoped to the protocol
3. global default (``team_id IS NULL``) profile scoped to the market
4. global default profile scoped to the protocol
5. no profile — every weight defaults to ``1.0`` at calc time

Ties at a single precedence level are broken by ``updated_at DESC`` so
the most recently edited profile wins. The resolver returns an empty
list when nothing matches; the calculator handles that as "no
overrides, weights default to 1.0".

A MARKET-scope profile pins one specific market within a protocol via
``(target_market_config_id, target_chain_id, target_market_id_hex)`` —
the protocol row owns the FK and the chain/market columns disambiguate
which of its discovered markets the profile applies to.

The resolution runs as a single SQL query that uses a ``CASE`` to
project each candidate's precedence, then orders by the projected
column. Picking the winner this way avoids 4+ round-trips.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, case, desc, or_, select
from sqlalchemy.sql import false

from cert_ra.db.models import WeightingProfile, WeightingProfileEntry
from cert_ra.types import WeightingProfileScope

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ("resolve_weighting_profile", "resolve_weighting_profile_entries")


async def resolve_weighting_profile(
    session: AsyncSession,
    *,
    protocol: str,
    market_config_id: UUID,
    chain_id: int,
    market_id_hex: str,
    team_id: UUID | None,
) -> WeightingProfile | None:
    """Return the most-specific profile applicable to the market, or ``None``.

    Precedence (winner-first):
    team+market → team+protocol → global+market → global+protocol.
    Ties at a level break on ``updated_at DESC``.

    A MARKET-scope candidate must match all three of
    ``(target_market_config_id, target_chain_id, target_market_id_hex)``
    so a profile registered for one market in a protocol does not leak
    into the others.

    Args:
        session: An open async SQLAlchemy session.
        protocol: Protocol slug of the market being scored.
        market_config_id: The protocol row's UUID.
        chain_id: Chain id of the specific market.
        market_id_hex: On-chain hex id of the specific market.
        team_id: The viewing user's current team. ``None`` means
            "anonymous / no team" — only global defaults apply.

    Returns:
        A loaded :class:`WeightingProfile` (with its ``entries``
        relationship lazily loadable via ``selectin``) or ``None`` when
        no profile matches.
    """
    market_match = and_(
        WeightingProfile.scope == WeightingProfileScope.MARKET,
        WeightingProfile.target_market_config_id == market_config_id,
        WeightingProfile.target_chain_id == chain_id,
        WeightingProfile.target_market_id_hex == market_id_hex,
    )
    protocol_match = and_(
        WeightingProfile.scope == WeightingProfileScope.PROTOCOL,
        WeightingProfile.target_protocol == protocol,
    )

    # Precedence projection: smaller integer = more specific.
    precedence = case(
        (
            and_(
                WeightingProfile.team_id == team_id if team_id is not None else false(),
                market_match,
            ),
            1,
        ),
        (
            and_(
                WeightingProfile.team_id == team_id if team_id is not None else false(),
                protocol_match,
            ),
            2,
        ),
        (
            and_(WeightingProfile.team_id.is_(None), market_match),
            3,
        ),
        (
            and_(WeightingProfile.team_id.is_(None), protocol_match),
            4,
        ),
        else_=None,
    )

    # Build the WHERE clauses that name candidate rows. We OR together
    # the four shapes — global ones are always considered; team-scoped
    # ones only when team_id is set.
    candidates = [
        and_(WeightingProfile.team_id.is_(None), market_match),
        and_(WeightingProfile.team_id.is_(None), protocol_match),
    ]
    if team_id is not None:
        candidates.extend(
            [
                and_(WeightingProfile.team_id == team_id, market_match),
                and_(WeightingProfile.team_id == team_id, protocol_match),
            ]
        )

    stmt = (
        select(WeightingProfile)
        .where(or_(*candidates))
        .order_by(precedence.asc(), desc(WeightingProfile.updated_at))
        .limit(1)
    )
    return (await session.scalars(stmt)).first()


async def resolve_weighting_profile_entries(
    session: AsyncSession,
    *,
    protocol: str,
    market_config_id: UUID,
    chain_id: int,
    market_id_hex: str,
    team_id: UUID | None,
) -> list[WeightingProfileEntry]:
    """Return the winning profile's entries, or ``[]`` when nothing applies.

    Convenience wrapper for the PD calculator: it consumes line items,
    not the profile header. A separate ``SELECT`` for entries keeps the
    precedence query simple — ``selectin`` lazy-loading on
    ``WeightingProfile.entries`` would also work, but the explicit
    query gives us deterministic ordering for tests.
    """
    profile = await resolve_weighting_profile(
        session,
        protocol=protocol,
        market_config_id=market_config_id,
        chain_id=chain_id,
        market_id_hex=market_id_hex,
        team_id=team_id,
    )
    if profile is None:
        return []
    stmt = (
        select(WeightingProfileEntry)
        .where(WeightingProfileEntry.weighting_profile_id == profile.id)
        .order_by(WeightingProfileEntry.category, WeightingProfileEntry.sub_category)
    )
    return list((await session.scalars(stmt)).all())
