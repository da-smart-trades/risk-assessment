# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from decimal import Decimal  # noqa: TC003
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.types import WeightingProfileEntryCategory, WeightingProfileScope

if TYPE_CHECKING:
    from .market_config import MarketConfig
    from .team import Team
    from .user import User


class WeightingProfile(UUIDAuditBase):
    """A named set of weight overrides for the market PD calculator.

    Either team-owned (``team_id`` set) or a global default
    (``team_id IS NULL``). Scoped to a single market
    (``scope='MARKET'`` with ``target_market_config_id`` set plus
    ``target_chain_id`` and ``target_market_id_hex`` pinning the
    specific market within the protocol — ``target_label`` caches the
    yarn label for the editor UI) or to every market for a protocol
    (``scope='PROTOCOL'`` with ``target_protocol`` set).

    The profile is a *partial overlay* of weight overrides — operators
    add as many ``(category, sub_category, weight)`` line items as they
    want via ``entries``. Any combination not present in the profile
    defaults to weight ``1.0`` at calculation time, so it is fine to
    customise only a subset.

    Resolution precedence at calculation time (most specific to least):
    team+market → team+protocol → global+market → global+protocol → none.
    """

    __tablename__ = "weighting_profile"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "scope",
            "target_protocol",
            "target_market_config_id",
            "target_chain_id",
            "target_market_id_hex",
            "name",
            name="uq_weighting_profile_team_scope_target_name",
        ),
        CheckConstraint(
            "(scope = 'MARKET' AND target_market_config_id IS NOT NULL "
            "AND target_chain_id IS NOT NULL "
            "AND target_market_id_hex IS NOT NULL "
            "AND target_label IS NOT NULL "
            "AND target_protocol IS NULL) "
            "OR (scope = 'PROTOCOL' AND target_protocol IS NOT NULL "
            "AND target_market_config_id IS NULL "
            "AND target_chain_id IS NULL "
            "AND target_market_id_hex IS NULL "
            "AND target_label IS NULL)",
            name="ck_weighting_profile_scope_target",
        ),
    )

    team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[WeightingProfileScope] = mapped_column(
        ENUM(WeightingProfileScope, name="weightingprofilescope"), nullable=False
    )
    target_protocol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_market_config_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("market_config.id", ondelete="CASCADE"), nullable=True, index=True
    )
    target_chain_id: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    target_market_id_hex: Mapped[str | None] = mapped_column(String(66), nullable=True)
    target_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )

    team: Mapped[Team | None] = relationship(lazy="joined", foreign_keys=[team_id])
    target_market: Mapped[MarketConfig | None] = relationship(
        lazy="joined", foreign_keys=[target_market_config_id]
    )
    creator: Mapped[User] = relationship(lazy="joined", foreign_keys=[created_by])
    updater: Mapped[User] = relationship(lazy="joined", foreign_keys=[updated_by])
    entries: Mapped[list[WeightingProfileEntry]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class WeightingProfileEntry(UUIDAuditBase):
    """One ``(category, sub_category, weight)`` override line in a profile.

    A profile has zero or more entries. Any ``(category, sub_category)``
    not represented here defaults to weight ``1.0`` at calculation time.
    """

    __tablename__ = "weighting_profile_entry"
    __table_args__ = (
        UniqueConstraint(
            "weighting_profile_id",
            "category",
            "sub_category",
            name="uq_weighting_profile_entry_natural_key",
        ),
        CheckConstraint("weight >= 0", name="ck_weighting_profile_entry_weight_nonneg"),
    )

    weighting_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("weighting_profile.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[WeightingProfileEntryCategory] = mapped_column(
        ENUM(WeightingProfileEntryCategory, name="weightingprofileentrycategory"),
        nullable=False,
    )
    sub_category: Mapped[str] = mapped_column(String(255), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)

    profile: Mapped[WeightingProfile] = relationship(
        back_populates="entries", foreign_keys=[weighting_profile_id]
    )
