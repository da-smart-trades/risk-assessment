# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Atomic entry replacement for weighting profiles.

``WeightingProfileService.replace_entries`` backs the "submit-the-whole-
form" edit flow: every save wipes the existing entries and re-inserts the
posted set. The unit of work emits INSERTs ahead of DELETEs within a
single flush, so a resubmitted ``(category, sub_category)`` that already
exists would collide with the row still pending deletion
(``uq_weighting_profile_entry_natural_key``). The service flushes the
clear() before re-inserting to avoid that.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cert_ra.api.domain.weighting_profiles.services import WeightingProfileService
from cert_ra.db.models import (
    MarketConfig,
    User,
    WeightingProfile,
    WeightingProfileEntry,
)
from cert_ra.types import WeightingProfileEntryCategory, WeightingProfileScope

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

_CHAIN_ID = 1
_MARKET_HEX = "0x" + "22" * 32


async def _make_profile(
    session: AsyncSession, entries: list[tuple[WeightingProfileEntryCategory, str, str]]
) -> WeightingProfile:
    """Create a market-scoped profile seeded with ``(category, sub, weight)`` rows."""
    author = User(email="rep-author@example.com", is_active=True, is_verified=True)
    session.add(author)
    await session.flush()
    market = MarketConfig(
        protocol="aave",
        enabled=True,
        created_by=author.id,
        updated_by=author.id,
    )
    session.add(market)
    await session.flush()
    profile = WeightingProfile(
        team_id=None,
        name="Custom",
        scope=WeightingProfileScope.MARKET,
        target_market_config_id=market.id,
        target_chain_id=_CHAIN_ID,
        target_market_id_hex=_MARKET_HEX,
        target_label="Aave USDC",
        created_by=author.id,
        updated_by=author.id,
    )
    for category, sub, weight in entries:
        profile.entries.append(
            WeightingProfileEntry(
                category=category, sub_category=sub, weight=Decimal(weight)
            )
        )
    session.add(profile)
    await session.flush()
    return profile


async def test_replace_entries_reusing_natural_key_does_not_collide(
    session: AsyncSession,
) -> None:
    """Resubmitting an existing (category, sub_category) + adding a new one saves.

    Regression: the old code inserted before the orphan deletes flushed,
    violating uq_weighting_profile_entry_natural_key on the reused key.
    """
    service = WeightingProfileService(session=session)
    profile = await _make_profile(
        session,
        [
            (WeightingProfileEntryCategory.CONTROL, "collateralDiversification", "1.0"),
            (WeightingProfileEntryCategory.ANCHOR, "marketSolvency", "1.0"),
        ],
    )

    # The form resubmits the two existing rows (same natural keys, new
    # weights) and adds a brand-new ASSURANCE dimension.
    await service.replace_entries(
        profile,
        [
            {
                "category": WeightingProfileEntryCategory.CONTROL,
                "sub_category": "collateralDiversification",
                "weight": "0.98",
            },
            {
                "category": WeightingProfileEntryCategory.ANCHOR,
                "sub_category": "marketSolvency",
                "weight": "1.05",
            },
            {
                "category": WeightingProfileEntryCategory.ASSURANCE,
                "sub_category": "Formal verification",
                "weight": "0.8",
            },
        ],
    )
    await session.commit()

    reloaded = await service.get_with_entries(profile.id)
    by_key = {(e.category.value, e.sub_category): e.weight for e in reloaded.entries}
    assert by_key == {
        ("CONTROL", "collateralDiversification"): Decimal("0.9800"),
        ("ANCHOR", "marketSolvency"): Decimal("1.0500"),
        ("ASSURANCE", "Formal verification"): Decimal("0.8000"),
    }


async def test_replace_entries_drops_removed_rows(session: AsyncSession) -> None:
    """Entries omitted from the new set are deleted (delete-orphan cascade)."""
    service = WeightingProfileService(session=session)
    profile = await _make_profile(
        session,
        [
            (WeightingProfileEntryCategory.ANCHOR, "marketSolvency", "1.0"),
            (WeightingProfileEntryCategory.ANCHOR, "liquidity", "1.0"),
        ],
    )

    await service.replace_entries(
        profile,
        [
            {
                "category": WeightingProfileEntryCategory.ANCHOR,
                "sub_category": "marketSolvency",
                "weight": "1.2",
            }
        ],
    )
    await session.commit()

    reloaded = await service.get_with_entries(profile.id)
    assert [(e.category.value, e.sub_category) for e in reloaded.entries] == [
        ("ANCHOR", "marketSolvency")
    ]
