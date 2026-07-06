# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.types import MarketSnapshotKind

if TYPE_CHECKING:
    from .market_config import MarketConfig


class AutomatedMarketSnapshot(UUIDAuditBase):
    """Time-series snapshot produced by the market collector or scorer.

    ``kind = 'COLLECT'`` rows store the 5-minute collector output: the
    two top-level dicts ``anchors`` and ``modifiers``, each a category →
    metric-dict tree (e.g. ``anchors.marketSolvency.totalSupplied``).

    ``kind = 'SCORE'`` rows are written by the hourly scorer; they carry
    the same ``anchors`` + ``modifiers`` and additionally populate
    ``score`` with the JSON returned by ``yarn <protocol> --score ...``.
    The latest score row drives the PD summary card on ``market/show``
    and the historical series powers the score-trend chart.

    Each row carries ``(chain_id, market_id_hex, label)`` denormalised
    from the yarn list output that produced the tick — ``market_config``
    only identifies the protocol now, so the snapshot row is the
    authoritative record of which specific market this PD applies to
    and what to show users when they look at it.
    """

    __tablename__ = "automated_market_snapshot"
    __table_args__ = (
        Index(
            "ix_amk_snapshot_market_kind_created",
            "market_config_id",
            "chain_id",
            "market_id_hex",
            "kind",
            "created_at",
        ),
        CheckConstraint(
            "(kind = 'SCORE' AND score IS NOT NULL) "
            "OR (kind = 'COLLECT' AND score IS NULL)",
            name="ck_amk_snapshot_score_for_score_kind",
        ),
    )

    market_config_id: Mapped[UUID] = mapped_column(
        # CASCADE so deleting a ``MarketConfig`` cleanly removes its
        # snapshot history. Before this was ``RESTRICT`` and made
        # deletion practically impossible without a manual purge.
        ForeignKey("market_config.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chain_id: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    market_id_hex: Mapped[str] = mapped_column(String(66), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[MarketSnapshotKind] = mapped_column(
        ENUM(MarketSnapshotKind, name="marketsnapshotkind"),
        nullable=False,
        index=True,
    )
    anchors: Mapped[dict] = mapped_column(JSONB, nullable=False)
    modifiers: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # ``none_as_null=True`` so a COLLECT row's ``score=None`` is written
    # as SQL NULL rather than a JSONB ``'null'`` scalar — the latter is
    # not ``IS NULL`` and would violate ``ck_amk_snapshot_score_for_score_kind``
    # (which requires ``score IS NULL`` when ``kind = 'COLLECT'``).
    score: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    market_config: Mapped[MarketConfig] = relationship(
        lazy="joined", foreign_keys=[market_config_id]
    )
