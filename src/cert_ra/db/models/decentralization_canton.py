# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column


class DecentralizationCanton(UUIDAuditBase):
    """Governance-decentralization snapshot for the Canton Super-Validator set.

    Canton SVs vote with equal (one-SV-one-vote) BFT power, so this records the
    count-based governance Nakamoto coefficient rather than the stake-weighted
    concentration measures used for PoS chains (see
    ``cert_ra.metrics.canton.schemas.CantonDecentralizationResult``).
    """

    __tablename__ = "decentralization_canton"
    __table_args__ = (Index("ix_decentralization_canton_created_at", "created_at"),)

    sv_count: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    validator_count: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    voting_threshold: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    gov_nakamoto_safety: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    gov_nakamoto_liveness: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    distinct_sequencer_count: Mapped[int] = mapped_column(BigInteger(), nullable=False)
