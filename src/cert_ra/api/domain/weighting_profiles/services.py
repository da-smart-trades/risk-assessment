# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Service + repository for weighting_profile + weighting_profile_entry.

Two related models are managed here:

* ``WeightingProfile`` is the header — name, scope, target, team ownership.
* ``WeightingProfileEntry`` is each ``(category, sub_category, weight)``
  override line. The entries collection is replaced atomically on
  every update so the form stays "submit-the-whole-form" simple; the
  ``cascade='all, delete-orphan'`` relationship on the model takes care
  of removing dropped entries.

The service surface mirrors :class:`MarketConfigService`: input
validation up front (scope ↔ target mutual exclusion, weight
non-negativity), then a clean ``RepositoryError`` rather than an
IntegrityError when a write would violate a CHECK constraint.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.exceptions import RepositoryError
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import (
    SQLAlchemyAsyncRepositoryService,
    schema_dump,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from cert_ra.db.models import (
    WeightingProfile,
    WeightingProfileEntry,
)
from cert_ra.types import WeightingProfileScope

if TYPE_CHECKING:
    from advanced_alchemy.service import ModelDictT

__all__ = ("WeightingProfileService",)


class WeightingProfileService(SQLAlchemyAsyncRepositoryService[WeightingProfile]):
    """CRUD + entry-replacement for weighting profiles."""

    class Repo(SQLAlchemyAsyncRepository[WeightingProfile]):
        """WeightingProfile SQLAlchemy Repository."""

        model_type = WeightingProfile

    repository_type = Repo

    async def to_model_on_create(
        self, data: ModelDictT[WeightingProfile]
    ) -> ModelDictT[WeightingProfile]:
        """Validate scope/target invariants and entry weights up front."""
        data = schema_dump(data)
        self._validate_scope_target(data)
        self._validate_entries(data.get("entries", []))
        return data

    async def to_model_on_update(
        self, data: ModelDictT[WeightingProfile]
    ) -> ModelDictT[WeightingProfile]:
        """Same validation on update — scope is immutable but target may change."""
        data = schema_dump(data)
        if "entries" in data:
            self._validate_entries(data["entries"])
        if "scope" in data:
            msg = "scope is immutable; create a new profile to change scope."
            raise RepositoryError(msg)
        return data

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_scope_target(data: dict) -> None:
        """Mirror ``ck_weighting_profile_scope_target`` so the error is friendly."""
        scope = data.get("scope")
        target_protocol = data.get("target_protocol")
        target_market_config_id = data.get("target_market_config_id")
        target_chain_id = data.get("target_chain_id")
        target_market_id_hex = data.get("target_market_id_hex")
        target_label = data.get("target_label")
        if scope == WeightingProfileScope.MARKET:
            missing = [
                field
                for field, value in (
                    ("target_market_config_id", target_market_config_id),
                    ("target_chain_id", target_chain_id),
                    ("target_market_id_hex", target_market_id_hex),
                    ("target_label", target_label),
                )
                if value is None
            ]
            if missing:
                msg = (
                    "scope='MARKET' requires "
                    f"{', '.join(missing)} (the protocol row + the specific "
                    "market within it)."
                )
                raise RepositoryError(msg)
            if target_protocol is not None:
                msg = (
                    "scope='MARKET' must leave target_protocol unset "
                    "(it's inferred from the market)."
                )
                raise RepositoryError(msg)
        elif scope == WeightingProfileScope.PROTOCOL:
            if target_protocol is None:
                msg = "scope='PROTOCOL' requires target_protocol."
                raise RepositoryError(msg)
            set_market_fields = [
                field
                for field, value in (
                    ("target_market_config_id", target_market_config_id),
                    ("target_chain_id", target_chain_id),
                    ("target_market_id_hex", target_market_id_hex),
                    ("target_label", target_label),
                )
                if value is not None
            ]
            if set_market_fields:
                msg = (
                    f"scope='PROTOCOL' must leave {', '.join(set_market_fields)} unset."
                )
                raise RepositoryError(msg)
        else:
            msg = f"unknown scope {scope!r}"
            raise RepositoryError(msg)

    @staticmethod
    def _validate_entries(entries: list) -> None:
        """Each entry needs a non-negative weight; duplicates are rejected."""
        seen: set[tuple[str, str]] = set()
        for raw in entries:
            entry = schema_dump(raw)
            weight = entry.get("weight")
            if weight is None:
                msg = "weighting_profile_entry requires a weight."
                raise RepositoryError(msg)
            try:
                value = Decimal(str(weight))
            except (TypeError, ValueError) as exc:
                msg = f"entry weight {weight!r} is not a valid decimal."
                raise RepositoryError(msg) from exc
            if value < 0:
                msg = f"entry weight {value} must be non-negative."
                raise RepositoryError(msg)
            key = (str(entry.get("category")), entry.get("sub_category") or "")
            if key in seen:
                msg = (
                    f"duplicate weighting_profile_entry for "
                    f"category={key[0]} sub_category={key[1]!r}."
                )
                raise RepositoryError(msg)
            seen.add(key)

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    async def get_with_entries(self, profile_id: UUID) -> WeightingProfile:
        """Fetch one profile with its entries pre-loaded (no lazy-load surprises)."""
        stmt = (
            select(WeightingProfile)
            .where(WeightingProfile.id == profile_id)
            .options(selectinload(WeightingProfile.entries))
        )
        result = await self.repository.session.execute(stmt)
        row: WeightingProfile | None = result.scalar_one_or_none()
        if row is None:
            msg = f"WeightingProfile {profile_id} does not exist."
            raise RepositoryError(msg)
        return row

    async def replace_entries(
        self,
        profile: WeightingProfile,
        new_entries: list[dict],
    ) -> WeightingProfile:
        """Atomically replace ``profile.entries`` with the supplied list.

        Existing entries are deleted via the ORM relationship's
        ``delete-orphan`` cascade. Callers should run this inside the
        same session/transaction as the profile mutation so partial
        failures roll back together.
        """
        session = self.repository.session
        # Wipe the existing collection — cascade='all, delete-orphan'
        # marks the orphaned rows for deletion on flush.
        profile.entries.clear()
        # Flush the DELETEs *before* re-inserting. The unit of work emits
        # INSERTs ahead of DELETEs within one flush, so a resubmitted entry
        # reusing an existing (category, sub_category) natural key would
        # otherwise collide with the row still pending deletion
        # (uq_weighting_profile_entry_natural_key).
        await session.flush()
        for raw in new_entries:
            entry = schema_dump(raw)
            profile.entries.append(
                WeightingProfileEntry(
                    category=entry["category"],
                    sub_category=entry["sub_category"],
                    weight=Decimal(str(entry["weight"])),
                )
            )
        await session.flush()
        return profile
