# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from .team import Team
    from .user import User
    from .user_favorite_metric import UserFavoriteMetric


class DashboardVisibility(StrEnum):
    """Who can see a saved dashboard ("named home page").

    - PRIVATE: only the owner sees it.
    - TEAM: every member of ``team_id`` sees it (read-only); the owner still
      controls its contents. Requires ``team_id`` to be set, enforced by
      ``ck_dashboard_team_visibility_requires_team``.
    """

    PRIVATE = "private"
    TEAM = "team"


class Dashboard(UUIDAuditBase):
    """A named, ownable home page — an ordered collection of favorite metrics.

    Each user has one or more dashboards; exactly one is their default home
    page (``is_default``). A dashboard may be shared with the owner's team by
    flipping ``visibility`` to ``TEAM`` (which pins ``team_id``); teammates then
    see it read-only in their picker, reflecting the owner's live edits.

    The favorites themselves live in ``UserFavoriteMetric`` rows that point back
    here via ``dashboard_id``.
    """

    __tablename__ = "dashboard"
    __table_args__ = (
        # A user can't have two dashboards with the same name.
        UniqueConstraint("owner_id", "name", name="uq_dashboard_owner_name"),
        # At most one default dashboard per owner.
        Index(
            "uq_dashboard_owner_default",
            "owner_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        # TEAM visibility requires a team to share into.
        CheckConstraint(
            "visibility <> 'team' OR team_id IS NOT NULL",
            name="ck_dashboard_team_visibility_requires_team",
        ),
        Index("ix_dashboard_owner_id", "owner_id"),
        Index("ix_dashboard_team_id", "team_id"),
    )

    name: Mapped[str] = mapped_column(String(length=120), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), nullable=True
    )
    visibility: Mapped[DashboardVisibility] = mapped_column(
        String(length=16),
        default=DashboardVisibility.PRIVATE,
        nullable=False,
    )
    is_default: Mapped[bool] = mapped_column(default=False, nullable=False)
    position: Mapped[int] = mapped_column(default=0, nullable=False)

    owner: Mapped[User] = relationship(lazy="noload", foreign_keys=[owner_id])
    team: Mapped[Team | None] = relationship(lazy="noload", foreign_keys=[team_id])
    items: Mapped[list[UserFavoriteMetric]] = relationship(
        back_populates="dashboard",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="(UserFavoriteMetric.position, UserFavoriteMetric.created_at)",
        lazy="noload",
    )
