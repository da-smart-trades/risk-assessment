# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from cert_ra.api.lib.schema import CamelizedBaseStruct

__all__ = (
    "SecurityReport",
    "SecurityReportListPage",
)


class SecurityReport(CamelizedBaseStruct):
    """Security report response schema."""

    id: UUID
    name: str
    description: str
    file_url: str
    uploaded_by: UUID
    uploader_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SecurityReportListPage(CamelizedBaseStruct):
    """Inertia page props for the security reports list."""

    items: list[SecurityReport]
    total: int
    is_operator_editor: bool
