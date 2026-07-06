# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import Index, Integer, String
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from cert_ra.types import ChainType


class GovernanceEvent(UUIDAuditBase):
    """Governance event count snapshot for a chain.

    ``event_type`` discriminates between the three categories of governance
    activity tracked across chains:

    - ``"proposals"`` — new improvement-proposal PRs / forum topics.
    - ``"execution"`` — regular DAO / multisig timelock execution events.
    - ``"emergency"`` — Security Council / bypass executor events.

    The ``count`` column holds the number of events observed in the polling
    window for that ``(chain, event_type)`` pair.
    """

    __tablename__ = "governance_event"
    __table_args__ = (
        Index(
            "ix_governance_event_chain_type_created_at",
            "chain",
            "event_type",
            "created_at",
        ),
    )

    chain: Mapped[ChainType] = mapped_column(
        ENUM(ChainType), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
