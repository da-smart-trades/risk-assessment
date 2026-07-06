# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, Float, Index
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cert_ra.types import ChainType


class DecentralizationOperatorSnapshot(UUIDAuditBase):
    """Per-chain snapshot of top staking operators and entity-grouped Nakamoto.

    Decentralization metrics computed against raw validator slots over-count
    decentralization on chains where one entity (Lido, Coinbase, etc.) runs
    many validators. This table stores the operator-grouped view alongside a
    coverage indicator: ``coverage_pct`` is the fraction of total stake mapped
    to a labelled entity — unmapped validators count as solo operators and so
    inflate the apparent decentralization.

    ``top_operators`` is a JSONB array of objects with the shape::

        {
            "rank": 1,
            "operatorId": "lido",
            "name": "Lido",
            "validatorCount": 312345,
            "stake": 9999000.0,
            "stakeShare": 0.298,
        }

    Keys are camelCase to match the API contract consumed by the frontend
    (see ``OperatorEntry`` in ``cert_ra.api.domain.metrics.schemas``).
    """

    __tablename__ = "decentralization_operator_snapshot"
    __table_args__ = (
        Index(
            "ix_decentralization_operator_chain_created_at",
            "chain",
            "created_at",
        ),
    )

    chain: Mapped[ChainType] = mapped_column(
        ENUM(ChainType), nullable=False, index=True
    )
    entity_nakamoto_liveness: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    entity_nakamoto_safety: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    entity_count: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    coverage_pct: Mapped[float] = mapped_column(Float(), nullable=False)
    top_operators: Mapped[list] = mapped_column(JSONB, nullable=False)
