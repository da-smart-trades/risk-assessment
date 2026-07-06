# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column


class FinalityEthereum(UUIDAuditBase):
    """Finality snapshot for Ethereum (execution layer + beacon chain)."""

    __tablename__ = "finality_ethereum"
    __table_args__ = (Index("ix_finality_ethereum_created_at", "created_at"),)

    head_height: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    finalized_height: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    safe_height: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    justified_epoch: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    finalized_epoch: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    justified_finalized_gap: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    time_since_finality_advance: Mapped[float] = mapped_column(nullable=False)
    head_to_finalized_time: Mapped[int] = mapped_column(BigInteger(), nullable=False)
