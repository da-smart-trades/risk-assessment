# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING

from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import SQLAlchemyAsyncRepositoryService, schema_dump

from cert_ra.db.models import SecurityReport

if TYPE_CHECKING:
    from advanced_alchemy.service import ModelDictT

__all__ = ("SecurityReportService",)


class SecurityReportService(SQLAlchemyAsyncRepositoryService[SecurityReport]):
    """CRUD service for operator-uploaded security reports."""

    class Repo(SQLAlchemyAsyncRepository[SecurityReport]):
        """SecurityReport SQLAlchemy repository."""

        model_type = SecurityReport

    repository_type = Repo
    match_fields = ["name"]  # noqa: RUF012

    async def to_model_on_create(
        self, data: ModelDictT[SecurityReport]
    ) -> ModelDictT[SecurityReport]:
        """Validate that ``uploaded_by`` was injected by the controller.

        Args:
            data: Raw payload for the new report.

        Returns:
            Validated payload.

        Raises:
            ValueError: If the controller forgot to inject ``uploaded_by``.
        """
        data = schema_dump(data)
        if "uploaded_by" not in data:
            msg = "uploaded_by must be set by the controller."
            raise ValueError(msg)
        return data
