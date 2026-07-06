# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from advanced_alchemy.types import FileObject  # noqa: TC002
from advanced_alchemy.types.file_object.data_type import StoredObject
from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from .user import User


class SecurityReport(UUIDAuditBase):
    """Operator-uploaded PDF security report (audit, risk assessment, compliance review)."""

    __tablename__ = "security_report"
    __table_args__ = (
        Index("ix_security_report_uploaded_by", "uploaded_by"),
        Index("ix_security_report_created_at", "created_at"),
    )

    name: Mapped[str] = mapped_column(String(length=255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    file: Mapped[FileObject] = mapped_column(
        StoredObject(backend="reports"), nullable=False
    )
    uploaded_by: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )

    uploader: Mapped[User] = relationship(lazy="joined", foreign_keys=[uploaded_by])
