# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.types import (
    ChainType,
    MetricCategory,
    ProtocolType,
    TokenType,
)

if TYPE_CHECKING:
    from .team import Team
    from .user import User


class ManualMetric(UUIDAuditBase):
    """Operator- or team-curated risk metric (free-form value, optional 1-5 score).

    A row with ``team_id IS NULL`` is operator-published and visible to every
    authenticated user. A row with ``team_id`` set is owned by that team and
    visible only to its members. The nullable FK alone discriminates — there
    is no boolean column.

    Deleting a team cascades to its team-owned manual metrics (consistent with
    ``Alert.team_id``).
    """

    __tablename__ = "manual_metric"
    __table_args__ = (
        Index("ix_manual_metric_chain_token_category", "chain", "token", "category"),
        CheckConstraint(
            "risk_score IS NULL OR (risk_score BETWEEN 1 AND 5)",
            name="ck_manual_metric_risk_score_range",
        ),
        CheckConstraint(
            "(chain IS NOT NULL AND token IS NULL AND protocol IS NULL "
            "AND category = 'GOVERNANCE') "
            "OR (chain IS NULL AND token IS NOT NULL AND protocol IS NULL "
            "AND category IN ('ANCHORS','CONTROL','ASSURANCE','TOKEN_RISK',"
            "'PROTOCOL_SCORE','TOKEN_SCORE')) "
            "OR (chain IS NULL AND token IS NULL AND protocol IS NOT NULL "
            "AND category IN ('ANCHORS','CONTROL','ASSURANCE','PROTOCOL_SCORE'))",
            name="ck_manual_metric_entity_category",
        ),
        # A market pin is optional and only meaningful for a protocol-scoped
        # ANCHORS row (the only kind that feeds a market's anchors term via
        # ``MarketConfig.assurance_protocol``). Both pin columns are set
        # together or not at all; an unpinned ANCHORS row applies to every
        # market of its protocol.
        CheckConstraint(
            "(market_chain_id IS NULL AND market_id_hex IS NULL) "
            "OR (market_chain_id IS NOT NULL AND market_id_hex IS NOT NULL "
            "AND protocol IS NOT NULL AND category = 'ANCHORS')",
            name="ck_manual_metric_market_pin",
        ),
    )

    team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
    )
    # Soft-delete flag. Deleted rows are excluded from every read query,
    # the UI, and all PD math. Used to retire rows without losing the data
    # (e.g. legacy ANCHORS rows retired when manual anchors went live).
    deleted: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(length=255), nullable=False)
    desc: Mapped[str] = mapped_column(Text, nullable=False)
    chain: Mapped[ChainType | None] = mapped_column(
        ENUM(ChainType, create_type=False), nullable=True, index=True
    )
    token: Mapped[TokenType | None] = mapped_column(
        ENUM(TokenType, name="tokentype"), nullable=True, index=True
    )
    protocol: Mapped[ProtocolType | None] = mapped_column(
        ENUM(ProtocolType, name="protocoltype", create_type=False),
        nullable=True,
        index=True,
    )
    category: Mapped[MetricCategory] = mapped_column(
        ENUM(MetricCategory, name="metriccategory", create_type=False),
        nullable=False,
        index=True,
    )
    sub_category: Mapped[str | None] = mapped_column(
        String(length=100), nullable=True, index=True
    )
    # Optional pin to one discovered market ``(chain_id, market_id_hex)``.
    # NULL/NULL ⇒ the row applies to every market of its ``protocol``.
    # Mirrors the types on ``automated_market_snapshot`` (BigInteger chain id,
    # 66-char hex id) so the pin matches a real market natural key.
    market_chain_id: Mapped[int | None] = mapped_column(
        BigInteger(), nullable=True, index=True
    )
    market_id_hex: Mapped[str | None] = mapped_column(
        String(length=66), nullable=True, index=True
    )
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_score: Mapped[int | None] = mapped_column(nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )

    team: Mapped[Team | None] = relationship(lazy="joined", foreign_keys=[team_id])
    creator: Mapped[User] = relationship(lazy="joined", foreign_keys=[created_by])
    updater: Mapped[User] = relationship(lazy="joined", foreign_keys=[updated_by])
