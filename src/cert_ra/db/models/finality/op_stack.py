# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, Index
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from cert_ra.types import ChainType


class FinalityOpStack(UUIDAuditBase):
    """Finality snapshot for OP Stack chains (unsafe → safe → finalized).

    Used for Ink and Unichain. Sourced from optimism_syncStatus RPC method.
    """

    __tablename__ = "finality_op_stack"
    __table_args__ = (
        Index("ix_finality_op_stack_chain_created_at", "chain", "created_at"),
    )

    chain: Mapped[ChainType] = mapped_column(
        ENUM(ChainType), nullable=False, index=True
    )
    unsafe_height: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    safe_height: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    finalized_height: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    unsafe_to_safe_blocks: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    safe_to_finalized_blocks: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    time_since_last_unsafe: Mapped[float] = mapped_column(nullable=False)
    height_correlation: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    time_to_hard_finality: Mapped[int] = mapped_column(BigInteger(), nullable=False)
