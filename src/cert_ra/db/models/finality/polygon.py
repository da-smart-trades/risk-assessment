# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column


class FinalityPolygon(UUIDAuditBase):
    """Finality snapshot for Polygon (latest → finalized, no safe stage)."""

    __tablename__ = "finality_polygon"
    __table_args__ = (Index("ix_finality_polygon_created_at", "created_at"),)

    latest_height: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    finalized_height: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    latest_to_finalized_blocks: Mapped[int] = mapped_column(
        BigInteger(), nullable=False
    )
    time_since_last_head: Mapped[float] = mapped_column(nullable=False)
