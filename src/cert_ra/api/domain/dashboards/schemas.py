# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Dashboard (named home page) request/response schemas."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from cert_ra.api.domain.favorites.schemas import ResolvedFavorite  # noqa: TC001
from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.db.models import DashboardVisibility

if TYPE_CHECKING:
    from cert_ra.db.models import Dashboard

__all__ = (
    "DashboardCreate",
    "DashboardPage",
    "DashboardSummary",
    "DashboardUpdate",
)


class DashboardSummary(CamelizedBaseStruct):
    """A saved dashboard as shown in the picker.

    ``is_owner`` distinguishes the current user's own pages from ones merely
    shared into their team (``is_shared``); only owners may edit. ``owner_name``
    is populated for shared pages so the picker can label them.
    """

    id: UUID
    name: str
    visibility: DashboardVisibility
    is_default: bool
    is_owner: bool
    is_shared: bool
    position: int
    created_at: datetime
    owner_name: str | None = None
    item_count: int | None = None

    @classmethod
    def from_model(
        cls, dashboard: Dashboard, *, current_user_id: UUID
    ) -> DashboardSummary:
        """Build a summary from an ORM row, computing ownership/sharing flags.

        ``owner_name`` is read from the (eager-loaded) owner only for shared
        pages — for the user's own pages the label is implicit.
        """
        is_owner = dashboard.owner_id == current_user_id
        return cls(
            id=dashboard.id,
            name=dashboard.name,
            visibility=dashboard.visibility,
            is_default=dashboard.is_default,
            is_owner=is_owner,
            is_shared=dashboard.visibility == DashboardVisibility.TEAM,
            position=dashboard.position,
            created_at=dashboard.created_at,
            owner_name=None if is_owner else getattr(dashboard.owner, "name", None),
        )


class DashboardPage(CamelizedBaseStruct):
    """Inertia page props for the dashboard home.

    ``current`` is the dashboard being viewed; ``favorites`` are its resolved
    cards; ``dashboards`` is the full picker list; ``can_edit`` is true only
    when the viewer owns ``current`` (shared pages render read-only).
    """

    current: DashboardSummary
    dashboards: list[DashboardSummary]
    favorites: list[ResolvedFavorite]
    can_edit: bool


class DashboardCreate(CamelizedBaseStruct):
    """Create a new (private) dashboard. Sharing is a follow-up update."""

    name: str


class DashboardUpdate(CamelizedBaseStruct):
    """Patch a dashboard's name, sharing, or default flag (owner only).

    Setting ``visibility = TEAM`` shares the page with the owner's current team;
    ``PRIVATE`` un-shares it. ``is_default = true`` makes it the owner's home
    page (clearing the previous default).
    """

    name: str | None = None
    visibility: DashboardVisibility | None = None
    is_default: bool | None = None
