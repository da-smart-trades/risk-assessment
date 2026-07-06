# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, Index
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from cert_ra.types import ChainType


class FinalityEvmL2(UUIDAuditBase):
    """Finality snapshot for standard EVM L2s (latest → safe → finalized).

    Used for Arbitrum and Base. height_correlation and time_to_hard_finality
    are nullable because Base does not compute them.
    """

    __tablename__ = "finality_evm_l2"
    __table_args__ = (
        Index("ix_finality_evm_l2_chain_created_at", "chain", "created_at"),
    )

    chain: Mapped[ChainType] = mapped_column(
        ENUM(ChainType), nullable=False, index=True
    )
    latest_height: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    safe_height: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    finalized_height: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    latest_to_safe_blocks: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    safe_to_finalized_blocks: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    time_since_last_head: Mapped[float] = mapped_column(nullable=False)
    height_correlation: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    time_to_hard_finality: Mapped[int | None] = mapped_column(
        BigInteger(), nullable=True
    )
