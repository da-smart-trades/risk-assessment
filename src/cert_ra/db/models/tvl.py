# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from decimal import Decimal  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import Index, Numeric
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from cert_ra.types import ChainType


class TVL(UUIDAuditBase):
    """Total Value Locked (TVL) snapshot for a chain.

    Sourced from DefiLlama's ``/v2/chains`` endpoint and persisted as a
    high-precision decimal so very large USD values are not subject to float
    rounding.
    """

    __tablename__ = "tvl"
    __table_args__ = (Index("ix_tvl_chain_created_at", "chain", "created_at"),)

    chain: Mapped[ChainType] = mapped_column(
        ENUM(ChainType), nullable=False, index=True
    )
    value: Mapped[Decimal] = mapped_column(
        Numeric(precision=30, scale=8), nullable=False
    )
