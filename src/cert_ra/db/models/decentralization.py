# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, Index
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from cert_ra.types import ChainType


class Decentralization(UUIDAuditBase):
    """Decentralization snapshot computed from per-validator stake distributions.

    All fields are derived from the same validator stake sample and are
    persisted together (see ``DecentralizationCombinedFetcher``).
    """

    __tablename__ = "decentralization"
    __table_args__ = (
        Index("ix_decentralization_chain_created_at", "chain", "created_at"),
    )

    chain: Mapped[ChainType] = mapped_column(
        ENUM(ChainType), nullable=False, index=True
    )
    total_amount_of_stakes: Mapped[float] = mapped_column(nullable=False)
    number_of_nodes: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    nakamoto_liveness_coefficient: Mapped[int] = mapped_column(
        BigInteger(), nullable=False
    )
    nakamoto_safety_coefficient: Mapped[int] = mapped_column(
        BigInteger(), nullable=False
    )
    hhi: Mapped[float] = mapped_column(nullable=False)
    shapley_top_value: Mapped[float] = mapped_column(nullable=False)
    shapley_second_value: Mapped[float] = mapped_column(nullable=False)
    shapley_third_value: Mapped[float] = mapped_column(nullable=False)
    renyi_entropy_alpha_0: Mapped[float] = mapped_column(nullable=False)
    renyi_entropy_alpha_1: Mapped[float] = mapped_column(nullable=False)
    renyi_entropy_alpha_2: Mapped[float] = mapped_column(nullable=False)
    renyi_entropy_alpha_inf: Mapped[float] = mapped_column(nullable=False)
