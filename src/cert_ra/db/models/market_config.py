# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.types import ProtocolType

if TYPE_CHECKING:
    from .user import User


class MarketConfig(UUIDAuditBase):
    """Operator-curated registration of one protocol the workers track.

    Replaces the legacy per-market admin rows: operators now configure
    *protocols* and the collector/scorer workflow discovers the live set
    of (chain_id, market_id_hex, label) per protocol on every tick by
    running ``yarn <protocol>``. The natural key is therefore just
    ``protocol`` — each protocol may have many concurrent markets in
    yarn output, but only one ``market_config`` row.

    ``protocol`` is always lowercase — the service layer lowercases on
    save and the CHECK regex defends against direct writes. Operators may
    type any case in the admin form.

    Per-market identifiers (chain_id, market_id_hex, label) live on the
    downstream snapshot / score / favorite / weighting_profile rows so
    every persisted artifact carries the exact market it refers to
    without re-querying yarn.
    """

    __tablename__ = "market_config"
    __table_args__ = (
        UniqueConstraint(
            "protocol",
            name="uq_market_config_protocol",
        ),
        CheckConstraint(
            "protocol ~ '^[a-z0-9_-]+$'",
            name="ck_market_config_protocol_lowercase_kebab",
        ),
    )

    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    assurance_protocol: Mapped[ProtocolType | None] = mapped_column(
        ENUM(ProtocolType, name="protocoltype", create_type=False),
        nullable=True,
    )
    """Operator-set mapping from this yarn ``protocol`` slug to the
    ``ProtocolType`` whose ASSURANCE manual metrics apply. ``None`` means
    the protocol has no ASSURANCE metrics — assurance contributes nothing
    to its PD."""
    enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=True,
        server_default=text("true"),
        index=True,
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )

    creator: Mapped[User] = relationship(lazy="joined", foreign_keys=[created_by])
    updater: Mapped[User] = relationship(lazy="joined", foreign_keys=[updated_by])
