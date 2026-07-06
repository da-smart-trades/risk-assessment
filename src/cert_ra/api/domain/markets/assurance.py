# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Shared ASSURANCE-metric resolution for market PD scoring.

A market's ``protocol`` is a lowercase yarn slug (e.g. ``aave``); ASSURANCE
manual metrics are keyed by the uppercase ``ProtocolType`` enum. The
operator maps the two at configure time via
``MarketConfig.assurance_protocol`` (a ``ProtocolType``, or ``None`` when
the protocol has no ASSURANCE metrics).

This module is the single place that reads that mapping and loads the
metrics — used by the scorer, the market detail page, and the dashboard
card resolver so all three agree (and none of them tries to compare a
lowercase slug against the ``protocoltype`` enum, which errors).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select

from cert_ra.db.models import ManualMetric
from cert_ra.types import MetricCategory

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from cert_ra.db.models import MarketConfig
    from cert_ra.types import ProtocolType

__all__ = (
    "assurance_protocol_for",
    "load_market_anchors",
    "load_protocol_assurance",
)


def assurance_protocol_for(market: MarketConfig) -> ProtocolType | None:
    """The ``ProtocolType`` an operator mapped this market's protocol to.

    ``None`` means the operator declared (at configure time) that the
    protocol has no ASSURANCE metrics, so assurance contributes nothing.
    """
    return market.assurance_protocol


async def load_protocol_assurance(
    session: AsyncSession, market: MarketConfig, team_id: UUID | None
) -> list[ManualMetric]:
    """Published ASSURANCE manual metrics for ``market``'s mapped protocol.

    Returns ``[]`` when the protocol has no ``ProtocolType`` mapping.
    Otherwise returns operator-published (shared, ``team_id IS NULL``) rows,
    plus the team's own published rows when ``team_id`` is set.
    """
    protocol = market.assurance_protocol
    if protocol is None:
        return []
    clauses = [
        ManualMetric.category == MetricCategory.ASSURANCE,
        ManualMetric.protocol == protocol,
        ManualMetric.is_published.is_(True),
        ManualMetric.deleted.is_(False),
    ]
    if team_id is None:
        clauses.append(ManualMetric.team_id.is_(None))
    else:
        clauses.append(
            or_(ManualMetric.team_id.is_(None), ManualMetric.team_id == team_id)
        )
    stmt = (
        select(ManualMetric)
        .where(*clauses)
        .order_by(ManualMetric.sub_category, ManualMetric.name)
    )
    return list((await session.scalars(stmt)).all())


async def load_market_anchors(
    session: AsyncSession,
    market: MarketConfig,
    *,
    chain_id: int,
    market_id_hex: str,
    team_id: UUID | None,
) -> list[ManualMetric]:
    """Published manual ANCHORS metrics that apply to one specific market.

    A manual ANCHORS row applies to a market when its ``protocol`` matches
    the market's ``assurance_protocol`` mapping AND it is either unpinned
    (``market_chain_id``/``market_id_hex`` both NULL ⇒ every market of the
    protocol) or pinned to exactly this ``(chain_id, market_id_hex)``.

    Returns ``[]`` when the protocol has no ``ProtocolType`` mapping.
    Otherwise returns operator-published (shared, ``team_id IS NULL``) rows,
    plus the team's own published rows when ``team_id`` is set. Soft-deleted
    rows are always excluded.
    """
    protocol = market.assurance_protocol
    if protocol is None:
        return []
    pin_match = or_(
        and_(
            ManualMetric.market_chain_id.is_(None),
            ManualMetric.market_id_hex.is_(None),
        ),
        and_(
            ManualMetric.market_chain_id == chain_id,
            ManualMetric.market_id_hex == market_id_hex,
        ),
    )
    clauses = [
        ManualMetric.category == MetricCategory.ANCHORS,
        ManualMetric.protocol == protocol,
        ManualMetric.is_published.is_(True),
        ManualMetric.deleted.is_(False),
        pin_match,
    ]
    if team_id is None:
        clauses.append(ManualMetric.team_id.is_(None))
    else:
        clauses.append(
            or_(ManualMetric.team_id.is_(None), ManualMetric.team_id == team_id)
        )
    stmt = (
        select(ManualMetric)
        .where(*clauses)
        .order_by(ManualMetric.sub_category, ManualMetric.name)
    )
    return list((await session.scalars(stmt)).all())
