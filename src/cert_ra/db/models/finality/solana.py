# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column


class FinalitySolana(UUIDAuditBase):
    """Finality snapshot for Solana (processed → confirmed → finalized slot pipeline)."""

    __tablename__ = "finality_solana"
    __table_args__ = (Index("ix_finality_solana_created_at", "created_at"),)

    processed_slot: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    confirmed_slot: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    finalized_slot: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    confirmed_finalized_gap: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    processed_confirmed_gap: Mapped[int] = mapped_column(BigInteger(), nullable=False)
