# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from decimal import Decimal  # noqa: TC003
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from .automated_market_snapshot import AutomatedMarketSnapshot
    from .market_config import MarketConfig


class MarketScore(UUIDAuditBase):
    """A computed Probability-of-Default snapshot for one market at one moment.

    Inserted by the scorer activity after every successful SCORE-kind
    ``AutomatedMarketSnapshot`` write. Stored separately from the snapshot
    so the user-configurable weighting profile can change without
    rewriting history — each row pins the PD that was actually surfaced
    to users at that point in time.

    The ``breakdown`` JSONB column carries per-metric contributions for
    explainability on the show page; the three term columns
    (``anchors_term`` / ``control_term`` / ``assurance_term``) are
    denormalised for cheap aggregation queries (trend chart).

    Each row carries ``(chain_id, market_id_hex, label)`` denormalised
    from the yarn list output that drove the scoring tick — see
    :class:`AutomatedMarketSnapshot` for rationale.

    Display flow:

    * **Favorite star** — reads the most recent row per
      ``(market_config_id, chain_id, market_id_hex)`` and renders
      ``final_pd``.
    * **Show page PD card** — same lookup, plus the breakdown.
    * **Trend chart** — time series of ``final_pd`` over the last N rows.
    """

    __tablename__ = "market_score"
    __table_args__ = (
        Index(
            "ix_market_score_market_created",
            "market_config_id",
            "chain_id",
            "market_id_hex",
            "created_at",
        ),
        CheckConstraint(
            "final_pd >= 0",
            name="ck_market_score_final_pd_nonneg",
        ),
        CheckConstraint(
            "anchors_term >= 0",
            name="ck_market_score_anchors_term_nonneg",
        ),
        CheckConstraint(
            "control_term >= 0",
            name="ck_market_score_control_term_nonneg",
        ),
        CheckConstraint(
            "assurance_term >= 0",
            name="ck_market_score_assurance_term_nonneg",
        ),
    )

    market_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("market_config.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chain_id: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    market_id_hex: Mapped[str] = mapped_column(String(66), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    source_amk_snapshot_id: Mapped[UUID] = mapped_column(
        # CASCADE so an admin-driven market deletion (which cascades
        # through ``automated_market_snapshot``) also wipes the
        # downstream PD rows. Previously RESTRICT, which combined with
        # the snapshot RESTRICT made the entire deletion chain
        # unreachable from the admin UI.
        ForeignKey("automated_market_snapshot.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    final_pd: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    anchors_term: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    control_term: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    assurance_term: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False)

    market_config: Mapped[MarketConfig] = relationship(
        lazy="joined", foreign_keys=[market_config_id]
    )
    source_amk_snapshot: Mapped[AutomatedMarketSnapshot] = relationship(
        lazy="select", foreign_keys=[source_amk_snapshot_id]
    )
