# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from cert_ra.types import ChainType


class Throughput(UUIDAuditBase):
    """Throughput snapshot fetched together from the Dune transactions query.

    Holds gas price, transactions-per-second, and blocks-per-second measured
    over the same sampling window for a given chain.
    """

    __tablename__ = "throughput"
    __table_args__ = (Index("ix_throughput_chain_created_at", "chain", "created_at"),)

    chain: Mapped[ChainType] = mapped_column(
        ENUM(ChainType), nullable=False, index=True
    )
    gas_price: Mapped[float] = mapped_column(nullable=False)
    transactions_per_second: Mapped[float] = mapped_column(nullable=False)
    blocks_per_second: Mapped[float] = mapped_column(nullable=False)
