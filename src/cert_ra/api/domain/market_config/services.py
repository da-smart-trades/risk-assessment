# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Service + repository for the operator-curated market_config table.

CRUD lives on the service. One layer of input validation: ``protocol``
is lowercased on save so admins can type any case in the form, and
the DB CHECK ``ck_market_config_protocol_lowercase_kebab`` defends
against direct writes. Normalising here gives clean errors when the
admin enters something the regex rejects.

The natural key ``protocol`` is unique in the DB; the service surfaces
``RepositoryError`` for collisions so the controller can return a 400
instead of an IntegrityError 500.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from advanced_alchemy.exceptions import RepositoryError
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import (
    SQLAlchemyAsyncRepositoryService,
    schema_dump,
)
from sqlalchemy import select

from cert_ra.db.models import MarketConfig

if TYPE_CHECKING:
    from advanced_alchemy.service import ModelDictT

__all__ = ("MarketConfigService",)


_PROTOCOL_RE = re.compile(r"^[a-z0-9_-]+$")


class MarketConfigService(SQLAlchemyAsyncRepositoryService[MarketConfig]):
    """CRUD service for operator-curated market_config rows."""

    class Repo(SQLAlchemyAsyncRepository[MarketConfig]):
        """MarketConfig SQLAlchemy Repository."""

        model_type = MarketConfig

    repository_type = Repo

    async def to_model_on_create(
        self, data: ModelDictT[MarketConfig]
    ) -> ModelDictT[MarketConfig]:
        """Normalise + validate a create payload before it hits the DB."""
        data = schema_dump(data)
        self._normalise_and_validate(data)
        await self._reject_duplicate_protocol(data)
        return data

    async def to_model_on_update(
        self, data: ModelDictT[MarketConfig]
    ) -> ModelDictT[MarketConfig]:
        """Same protocol-format validation if protocol is present (it shouldn't be)."""
        data = schema_dump(data)
        self._normalise_and_validate(data, allow_partial=True)
        return data

    @staticmethod
    def _normalise_and_validate(data: dict, *, allow_partial: bool = False) -> None:
        """Lowercase the protocol; reject malformed protocol strings.

        Args:
            data: The mutable dict being prepared for insert/update.
            allow_partial: If ``True``, fields not present in ``data`` are
                ignored (update path). Otherwise missing required fields
                raise — but only the *format* checks; presence checks are
                left to the schema layer.

        Raises:
            RepositoryError: For lowercase / format violations.
        """
        protocol = data.get("protocol")
        if protocol is not None:
            normalised = protocol.strip().lower()
            data["protocol"] = normalised
            if not _PROTOCOL_RE.match(normalised):
                msg = (
                    f"protocol {protocol!r} must contain only lowercase "
                    f"letters, digits, underscores, and hyphens"
                )
                raise RepositoryError(msg)
        elif not allow_partial:
            msg = "protocol is required"
            raise RepositoryError(msg)

    async def _reject_duplicate_protocol(self, data: dict) -> None:
        """Surface ``RepositoryError`` instead of a UniqueViolation 500."""
        stmt = select(MarketConfig.id).where(MarketConfig.protocol == data["protocol"])
        existing = (await self.repository.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            msg = f"A market_config with protocol={data['protocol']!r} already exists."
            raise RepositoryError(msg)
