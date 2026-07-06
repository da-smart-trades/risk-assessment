# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.types import ChainType, MetricType, TokenType

if TYPE_CHECKING:
    from .dashboard import Dashboard
    from .manual_metric import ManualMetric
    from .market_config import MarketConfig


class UserFavoriteMetric(UUIDAuditBase):
    """A favorite metric pinned to a :class:`Dashboard` (named home page).

    A favorite points at exactly one of three targets:

    * an auto-collected metric series, identified by the
      ``(metric_type, chain, token)`` tuple (mirrors ``Alert``'s
      addressing scheme);
    * a single ``ManualMetric`` row (shared / operator-published
      PROTOCOL_SCORE rows only — enforced at the service layer);
    * a specific market under a registered protocol, identified by
      ``(market_config_id, favorite_chain_id, favorite_market_id_hex)``.
      ``market_config_id`` names the protocol;
      ``favorite_chain_id`` / ``favorite_market_id_hex`` pin the specific
      market within it, and ``favorite_label`` caches the human label
      from the yarn list output at favorite-creation time so the
      dashboard card can render it without re-running yarn.

    The XOR is enforced by ``ck_user_favorite_metric_target_xor``. The
    additional CHECK ``ck_user_favorite_metric_market_fields`` requires
    favorite_chain_id / favorite_market_id_hex / favorite_label to be
    non-NULL exactly when market_config_id is set.

    Favorites belong to a dashboard (not directly to a user); ownership
    is derived via ``Dashboard.owner_id``. ``position`` orders the
    cards within the dashboard grid.
    """

    __tablename__ = "user_favorite_metric"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN metric_type IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN manual_metric_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN market_config_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_user_favorite_metric_target_xor",
        ),
        CheckConstraint(
            "(market_config_id IS NULL "
            "AND favorite_chain_id IS NULL "
            "AND favorite_market_id_hex IS NULL "
            "AND favorite_label IS NULL) "
            "OR (market_config_id IS NOT NULL "
            "AND favorite_chain_id IS NOT NULL "
            "AND favorite_market_id_hex IS NOT NULL "
            "AND favorite_label IS NOT NULL)",
            name="ck_user_favorite_metric_market_fields",
        ),
        # Partial unique index for auto favorites: each
        # (metric_type, chain, token) tuple appears at most once per dashboard.
        # NULLS NOT DISTINCT collapses NULL chain/token within a metric_type.
        # The WHERE clause excludes manual and market favorites — without
        # it, multiple non-auto favorites for the same dashboard would
        # collide on (metric_type=NULL, chain=NULL, token=NULL).
        Index(
            "uq_user_favorite_metric_auto",
            "dashboard_id",
            "metric_type",
            "chain",
            "token",
            unique=True,
            postgresql_where=text(
                "manual_metric_id IS NULL AND market_config_id IS NULL"
            ),
            postgresql_nulls_not_distinct=True,
        ),
        # Partial unique index for manual favorites: each manual_metric_id
        # appears at most once per dashboard.
        Index(
            "uq_user_favorite_metric_manual",
            "dashboard_id",
            "manual_metric_id",
            unique=True,
            postgresql_where=text("manual_metric_id IS NOT NULL"),
        ),
        # Partial unique index for market favorites: each market
        # identity trio market_config_id + favorite_chain_id +
        # favorite_market_id_hex appears at most once per dashboard so
        # a user can't double-pin the same market on the same dashboard.
        Index(
            "uq_user_favorite_metric_market",
            "dashboard_id",
            "market_config_id",
            "favorite_chain_id",
            "favorite_market_id_hex",
            unique=True,
            postgresql_where=text("market_config_id IS NOT NULL"),
        ),
        Index("ix_user_favorite_metric_dashboard_id", "dashboard_id"),
    )

    dashboard_id: Mapped[UUID] = mapped_column(
        ForeignKey("dashboard.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(default=0, nullable=False)
    metric_type: Mapped[MetricType | None] = mapped_column(
        ENUM(MetricType, name="metrictype", create_type=False), nullable=True
    )
    chain: Mapped[ChainType | None] = mapped_column(
        ENUM(ChainType, name="chaintype", create_type=False), nullable=True
    )
    token: Mapped[TokenType | None] = mapped_column(
        ENUM(TokenType, name="tokentype", create_type=False), nullable=True
    )
    manual_metric_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("manual_metric.id", ondelete="CASCADE"), nullable=True
    )
    market_config_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("market_config.id", ondelete="CASCADE"), nullable=True
    )
    favorite_chain_id: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    favorite_market_id_hex: Mapped[str | None] = mapped_column(
        String(66), nullable=True
    )
    favorite_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    dashboard: Mapped[Dashboard] = relationship(
        back_populates="items", foreign_keys=[dashboard_id]
    )
    manual_metric: Mapped[ManualMetric | None] = relationship(
        lazy="joined", foreign_keys=[manual_metric_id]
    )
    market_config: Mapped[MarketConfig | None] = relationship(
        lazy="joined", foreign_keys=[market_config_id]
    )
