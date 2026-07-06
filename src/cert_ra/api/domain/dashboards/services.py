# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Dashboard service — CRUD plus ownership/visibility helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.exceptions import RepositoryError
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import (
    SQLAlchemyAsyncRepositoryService,
    schema_dump,
)
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import joinedload

from cert_ra.db.models import Dashboard, DashboardVisibility

if TYPE_CHECKING:
    from collections.abc import Sequence

    from advanced_alchemy.service import ModelDictT

__all__ = ("DashboardService",)


class DashboardService(SQLAlchemyAsyncRepositoryService[Dashboard]):
    """CRUD service for saved dashboards (named home pages)."""

    class Repo(SQLAlchemyAsyncRepository[Dashboard]):
        """Dashboard SQLAlchemy Repository."""

        model_type = Dashboard

    repository_type = Repo

    async def to_model_on_create(
        self, data: ModelDictT[Dashboard]
    ) -> ModelDictT[Dashboard]:
        """Validate + default a create payload.

        Ensures the controller injected ``owner_id`` and a non-blank ``name``,
        and makes the user's *first* dashboard their default home page.

        Raises:
            RepositoryError: If ``owner_id`` is missing or ``name`` is blank.
        """
        data = schema_dump(data)
        owner_id = data.get("owner_id")
        if owner_id is None:
            msg = "owner_id must be set by the controller."
            raise RepositoryError(msg)
        name = (data.get("name") or "").strip()
        if not name:
            msg = "Dashboard name must not be empty."
            raise RepositoryError(msg)
        data["name"] = name
        if "is_default" not in data:
            data["is_default"] = not await self._owner_has_any(owner_id)
        return data

    async def _owner_has_any(self, owner_id: UUID) -> bool:
        stmt = (
            select(func.count())
            .select_from(Dashboard)
            .where(Dashboard.owner_id == owner_id)
        )
        return bool((await self.repository.session.execute(stmt)).scalar_one())

    async def list_for_user(
        self, *, user_id: UUID, team_ids: Sequence[UUID]
    ) -> list[Dashboard]:
        """Return dashboards visible to a user, owner eagerly loaded.

        Visible = the user's own dashboards plus any ``TEAM``-visibility
        dashboard shared into a team they belong to. Owned pages sort first
        (by ``position``), then shared pages (newest first).
        """
        visible = Dashboard.owner_id == user_id
        if team_ids:
            visible = or_(
                visible,
                and_(
                    Dashboard.visibility == DashboardVisibility.TEAM,
                    Dashboard.team_id.in_(team_ids),
                    Dashboard.owner_id != user_id,
                ),
            )
        stmt = (
            select(Dashboard)
            .options(joinedload(Dashboard.owner))
            .where(visible)
            .order_by(
                (Dashboard.owner_id != user_id),  # own pages first
                Dashboard.position,
                Dashboard.created_at.desc(),
            )
        )
        return list((await self.repository.session.execute(stmt)).scalars().all())

    async def ensure_default(self, owner_id: UUID) -> Dashboard:
        """Return the user's default dashboard, creating one if none exists."""
        stmt = select(Dashboard).where(
            Dashboard.owner_id == owner_id, Dashboard.is_default.is_(True)
        )
        existing: Dashboard | None = (
            (await self.repository.session.execute(stmt)).scalars().first()
        )
        if existing is not None:
            return existing
        # No default yet (brand-new user, or all dashboards deleted) — make one.
        return await self.create(
            {"owner_id": owner_id, "name": "My favorites", "is_default": True}
        )

    async def set_default(self, *, dashboard: Dashboard, owner_id: UUID) -> None:
        """Make ``dashboard`` the owner's sole default home page."""
        if dashboard.is_default:
            return
        await self.repository.session.execute(
            update(Dashboard)
            .where(Dashboard.owner_id == owner_id, Dashboard.is_default.is_(True))
            .values(is_default=False)
        )
        dashboard.is_default = True
        await self.repository.session.flush()
