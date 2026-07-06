# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from cert_ra.types import ChainType


class TimeToFinality(UUIDAuditBase):
    """Soft time-to-finality snapshot (average seconds between new heads/slots).

    Sourced from WebSocket subscriptions (``newHeads`` / ``newFlashblocks`` for
    EVM chains, ``slotSubscribe`` for Solana).
    """

    __tablename__ = "time_to_finality"
    __table_args__ = (
        Index("ix_time_to_finality_chain_created_at", "chain", "created_at"),
    )

    chain: Mapped[ChainType] = mapped_column(
        ENUM(ChainType), nullable=False, index=True
    )
    soft_finality_seconds: Mapped[float] = mapped_column(nullable=False)
