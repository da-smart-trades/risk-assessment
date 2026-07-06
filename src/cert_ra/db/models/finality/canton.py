# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column


class FinalityCanton(UUIDAuditBase):
    """Combined finality snapshot for the Canton Global Synchronizer.

    Canton finality is deterministic, so rather than block-height gradients
    this row captures round cadence / ledger freshness alongside the SV BFT
    quorum margin (see ``cert_ra.metrics.canton.schemas.CantonFinalityResult``).
    """

    __tablename__ = "finality_canton"
    __table_args__ = (Index("ix_finality_canton_created_at", "created_at"),)

    latest_round_number: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    round_advance_seconds: Mapped[float] = mapped_column(nullable=False)
    round_window_seconds: Mapped[float] = mapped_column(nullable=False)
    open_round_count: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    ledger_freshness_seconds: Mapped[float] = mapped_column(nullable=False)
    live_sv_count: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    voting_threshold: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    sv_quorum_margin: Mapped[int] = mapped_column(BigInteger(), nullable=False)
