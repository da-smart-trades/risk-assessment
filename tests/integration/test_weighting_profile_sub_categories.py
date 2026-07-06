# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Cascading sub-category dropdown for the weighting-profile form.

Regression coverage for two bugs in
``GET /api/weighting-profiles/available-sub-categories``:

* The form must send the category enum value verbatim (uppercase, e.g.
  ``ANCHOR``). A lowercased value is rejected by Litestar with a 400,
  which the form silently swallowed into an empty option list — so the
  dropdown never populated for a protocol-scoped profile.
* For ``CONTROL`` the endpoint must read the scorer's current
  ``controls`` block (older snapshots used ``controlModifiers``); a
  stale single-key lookup returned nothing against new snapshots.
* For ``ASSURANCE`` the endpoint must resolve the lowercase market slug
  to the uppercase ``ProtocolType`` via ``MarketConfig.assurance_protocol``
  before filtering ``ManualMetric.protocol`` — comparing the slug against
  the enum column directly matches nothing, so the dropdown stayed empty.
  The selectable value is the metric's ``name`` (the assurance dimension),
  not its ``sub_category`` (which only marks the Evidence/Multiplier pair).

The endpoint sits behind the MFA-enrollment trap, so we exercise the
handler's logic against a live session (no HTTP auth) and verify the
query-param casing contract against a minimal isolated app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from cert_ra.api.domain.admin.controllers._weighting_profiles import (
    WeightingProfileApiController,
)
from cert_ra.api.domain.weighting_profiles.services import WeightingProfileService
from cert_ra.db.models import (
    AutomatedMarketSnapshot,
    ManualMetric,
    MarketConfig,
    User,
)
from cert_ra.types import (
    MarketSnapshotKind,
    MetricCategory,
    ProtocolType,
    WeightingProfileEntryCategory,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

SUPERUSER_ID = UUID("97108ac1-ffcb-411d-8b1e-d9183399f63b")

_PROTOCOL = "aave-subcat"
_CHAIN_ID = 1
_MARKET_HEX = "0x" + "11" * 32

# The route handler's underlying coroutine; ``self`` is unused so we pass None.
_list_sub_categories = WeightingProfileApiController.__dict__[
    "list_available_sub_categories"
].fn


async def _seed_score_snapshot(session: AsyncSession, *, score: dict[str, Any]) -> None:
    """Create an enabled protocol row + one SCORE snapshot carrying ``score``."""
    market = MarketConfig(
        protocol=_PROTOCOL,
        enabled=True,
        created_by=SUPERUSER_ID,
        updated_by=SUPERUSER_ID,
    )
    session.add(market)
    await session.flush()
    session.add(
        AutomatedMarketSnapshot(
            market_config_id=market.id,
            chain_id=_CHAIN_ID,
            market_id_hex=_MARKET_HEX,
            label="Aave USDC",
            kind=MarketSnapshotKind.SCORE,
            anchors={},
            modifiers={},
            score=score,
        )
    )
    await session.flush()


async def test_anchor_sub_categories_populate_for_protocol_scope(
    session: AsyncSession,
) -> None:
    """Protocol-scope ``ANCHOR`` returns the anchor keys from the latest snapshot."""
    await _seed_score_snapshot(
        session,
        score={"anchors": {"utilization": {"pd": 0.2}, "liquidity": {"pd": 0.1}}},
    )
    page = await _list_sub_categories(
        None,
        weighting_profile_service=WeightingProfileService(session=session),
        category=WeightingProfileEntryCategory.ANCHOR,
        protocol=_PROTOCOL,
    )
    assert page.sub_categories == ["liquidity", "utilization"]


async def test_control_sub_categories_read_current_controls_key(
    session: AsyncSession,
) -> None:
    """``CONTROL`` reads the scorer's current ``controls`` block.

    Not just the legacy ``controlModifiers`` key.
    """
    await _seed_score_snapshot(
        session,
        score={"controls": {"oracle": {"multiplier": 1.1}}},
    )
    page = await _list_sub_categories(
        None,
        weighting_profile_service=WeightingProfileService(session=session),
        category=WeightingProfileEntryCategory.CONTROL,
        protocol=_PROTOCOL,
    )
    assert page.sub_categories == ["oracle"]


async def test_control_sub_categories_read_legacy_key(session: AsyncSession) -> None:
    """Older snapshots stored controls under ``controlModifiers``; still read."""
    await _seed_score_snapshot(
        session,
        score={"controlModifiers": {"legacy_control": {"multiplier": 0.9}}},
    )
    page = await _list_sub_categories(
        None,
        weighting_profile_service=WeightingProfileService(session=session),
        category=WeightingProfileEntryCategory.CONTROL,
        protocol=_PROTOCOL,
    )
    assert page.sub_categories == ["legacy_control"]


async def _seed_assurance_market(
    session: AsyncSession,
    *,
    slug: str,
    assurance_protocol: ProtocolType | None,
    dimensions: tuple[str, ...],
) -> UUID:
    """Enabled market mapped to ``assurance_protocol`` + one Multiplier row per dimension.

    Mirrors the real fixtures: each assurance dimension is the row's
    ``name``; the ``sub_category`` column only marks the Evidence/Multiplier
    pair. Returns the ``MarketConfig`` id.
    """
    author = User(email=f"assur-{slug}@example.com", is_active=True, is_verified=True)
    session.add(author)
    await session.flush()
    market = MarketConfig(
        protocol=slug,
        assurance_protocol=assurance_protocol,
        enabled=True,
        created_by=author.id,
        updated_by=author.id,
    )
    session.add(market)
    await session.flush()
    if assurance_protocol is not None:
        for dim in dimensions:
            session.add(
                ManualMetric(
                    name=dim,
                    desc="x",
                    category=MetricCategory.ASSURANCE,
                    protocol=assurance_protocol,
                    sub_category="Multiplier",
                    value="0.9",
                    is_published=True,
                    team_id=None,
                    created_by=author.id,
                    updated_by=author.id,
                )
            )
    await session.flush()
    return market.id


async def test_assurance_sub_categories_resolve_slug_to_protocol_type(
    session: AsyncSession,
) -> None:
    """Protocol-scope ASSURANCE maps the slug to its ProtocolType, returns dimension names.

    Regression: the slug ("aave-assur") was compared directly against the
    ``ManualMetric.protocol`` enum column (AAVE_V3), matching nothing.
    """
    await _seed_assurance_market(
        session,
        slug="aave-assur",
        assurance_protocol=ProtocolType.AAVE_V3,
        dimensions=("Audits", "Testing", "Monitoring"),
    )
    page = await _list_sub_categories(
        None,
        weighting_profile_service=WeightingProfileService(session=session),
        category=WeightingProfileEntryCategory.ASSURANCE,
        protocol="aave-assur",
    )
    # The selectable values are the dimension `name`s, sorted — not the
    # "Multiplier" sub_category the rows carry.
    assert page.sub_categories == ["Audits", "Monitoring", "Testing"]


async def test_assurance_sub_categories_market_scope(session: AsyncSession) -> None:
    """Market-scope ASSURANCE resolves the ProtocolType from ``market_config_id``."""
    market_id = await _seed_assurance_market(
        session,
        slug="morpho-assur",
        assurance_protocol=ProtocolType.MORPHO_V2,
        dimensions=("Audits", "Formal verification"),
    )
    page = await _list_sub_categories(
        None,
        weighting_profile_service=WeightingProfileService(session=session),
        category=WeightingProfileEntryCategory.ASSURANCE,
        protocol="morpho-assur",
        market_config_id=market_id,
    )
    assert page.sub_categories == ["Audits", "Formal verification"]


async def test_assurance_sub_categories_empty_when_unmapped(
    session: AsyncSession,
) -> None:
    """A market with no ``assurance_protocol`` mapping yields an empty list."""
    await _seed_assurance_market(
        session,
        slug="drift-assur",
        assurance_protocol=None,
        dimensions=(),
    )
    page = await _list_sub_categories(
        None,
        weighting_profile_service=WeightingProfileService(session=session),
        category=WeightingProfileEntryCategory.ASSURANCE,
        protocol="drift-assur",
    )
    assert page.sub_categories == []
