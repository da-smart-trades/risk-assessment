# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from cert_ra.types import ChainType


class Release(UUIDAuditBase):
    """Latest software release observed for a chain's reference repository.

    Polled from the GitHub ``releases/latest`` endpoint per ``(chain, repo)``
    pair. The package exists so future chains can opt in via ``worker.py``
    schedules; it is not currently registered as a Temporal schedule by
    default.
    """

    __tablename__ = "release"
    __table_args__ = (Index("ix_release_chain_created_at", "chain", "created_at"),)

    chain: Mapped[ChainType] = mapped_column(
        ENUM(ChainType), nullable=False, index=True
    )
    repo: Mapped[str] = mapped_column(String(200), nullable=False)
    released_at: Mapped[datetime] = mapped_column(nullable=False)
