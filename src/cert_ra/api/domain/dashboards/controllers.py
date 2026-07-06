# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Dashboard (named home page) JSON API.

CRUD for ``Dashboard`` plus dashboard-scoped favorite items. A user owns and
edits their own dashboards; teammates see ``TEAM``-visibility dashboards
read-only (no item endpoints succeed against a page they don't own).
"""

from __future__ import annotations

from uuid import UUID  # noqa: TC003  (runtime use in Litestar handler signatures)

from advanced_alchemy.extensions.litestar.providers import create_service_dependencies
from advanced_alchemy.filters import CollectionFilter, OrderBy
from litestar import Controller, Request, delete, get, patch, post
from litestar.exceptions import NotFoundException, PermissionDeniedException
from sqlalchemy import select

from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.dashboards.schemas import (
    DashboardCreate,
    DashboardSummary,
    DashboardUpdate,
)
from cert_ra.api.domain.dashboards.services import DashboardService
from cert_ra.api.domain.favorites.schemas import (
    Favorite,
    FavoriteAutoCreate,
    FavoriteManualCreate,
    FavoriteMarketCreate,
)
from cert_ra.api.domain.favorites.services import UserFavoriteMetricService
from cert_ra.api.lib.team_context import current_team_id_from_session
from cert_ra.db.models import (
    Dashboard,
    DashboardVisibility,
    User,
)

__all__ = ("DashboardApiController",)


def _derive_share_team_id(request: Request, user: User) -> UUID:
    """Pick the team a dashboard should be shared into.

    Mirrors manual-metric scope selection: a single-team user shares into that
    team; a multi-team user shares into their current (session) team.

    Raises:
        PermissionDeniedException: If the user belongs to no team.
        ValidationException-equivalent: If a multi-team user has no current
            team selected (raised as PermissionDenied for a clean 4xx).
    """
    team_ids = [m.team_id for m in user.teams]
    if not team_ids:
        msg = "You must belong to a team before sharing a dashboard."
        raise PermissionDeniedException(detail=msg)
    if len(team_ids) == 1:
        return team_ids[0]
    current_id = current_team_id_from_session(request.session)
    if current_id is None or current_id not in team_ids:
        msg = "Switch to the team you want to share this dashboard with first."
        raise PermissionDeniedException(detail=msg)
    return current_id


class DashboardApiController(Controller):
    """Dashboard CRUD + dashboard-scoped favorite items."""

    path = "/api/dashboards"
    tags = ["Dashboards"]  # noqa: RUF012
    guards = [requires_active_user]  # noqa: RUF012
    dependencies = {  # noqa: RUF012
        **create_service_dependencies(DashboardService, key="dashboards_service"),
        **create_service_dependencies(
            UserFavoriteMetricService, key="favorites_service"
        ),
    }
    signature_namespace = {  # noqa: RUF012
        "DashboardService": DashboardService,
        "UserFavoriteMetricService": UserFavoriteMetricService,
        "DashboardCreate": DashboardCreate,
        "DashboardUpdate": DashboardUpdate,
        "FavoriteAutoCreate": FavoriteAutoCreate,
        "FavoriteManualCreate": FavoriteManualCreate,
        "FavoriteMarketCreate": FavoriteMarketCreate,
    }

    @get(
        operation_id="ListDashboards",
        name="dashboards:list",
        summary="List dashboards visible to the current user",
        path="/",
    )
    async def list_dashboards(
        self,
        dashboards_service: DashboardService,
        current_user: User,
    ) -> list[DashboardSummary]:
        """List the user's own dashboards plus ones shared into their teams.

        Returns:
            Owned dashboards first, then team-shared ones.
        """
        team_ids = [m.team_id for m in current_user.teams]
        dashboards = await dashboards_service.list_for_user(
            user_id=current_user.id, team_ids=team_ids
        )
        return [
            DashboardSummary.from_model(d, current_user_id=current_user.id)
            for d in dashboards
        ]

    @post(
        operation_id="CreateDashboard",
        name="dashboards:create",
        summary="Create a new (private) dashboard",
        path="/",
    )
    async def create_dashboard(
        self,
        dashboards_service: DashboardService,
        current_user: User,
        data: DashboardCreate,
    ) -> DashboardSummary:
        """Create a private dashboard owned by the current user.

        Returns:
            The new dashboard's summary.
        """
        db_obj = await dashboards_service.create(
            {"owner_id": current_user.id, "name": data.name}
        )
        return DashboardSummary.from_model(db_obj, current_user_id=current_user.id)

    @patch(
        operation_id="UpdateDashboard",
        name="dashboards:update",
        summary="Rename, share, or set-default a dashboard (owner only)",
        path="/{dashboard_id:uuid}",
    )
    async def update_dashboard(
        self,
        dashboards_service: DashboardService,
        request: Request,
        current_user: User,
        data: DashboardUpdate,
        dashboard_id: UUID,
    ) -> DashboardSummary:
        """Patch a dashboard the current user owns.

        Returns:
            The updated dashboard's summary.
        """
        dashboard = await self._get_owned(
            dashboards_service, dashboard_id, current_user.id
        )
        if data.name is not None:
            stripped = data.name.strip()
            if not stripped:
                raise PermissionDeniedException(detail="Name must not be empty.")
            dashboard.name = stripped
        if data.visibility is not None:
            if data.visibility == DashboardVisibility.TEAM:
                dashboard.team_id = _derive_share_team_id(request, current_user)
                dashboard.visibility = DashboardVisibility.TEAM
            else:
                dashboard.visibility = DashboardVisibility.PRIVATE
                dashboard.team_id = None
        db_obj = await dashboards_service.update(dashboard, item_id=dashboard_id)
        if data.is_default:
            await dashboards_service.set_default(
                dashboard=db_obj, owner_id=current_user.id
            )
        return DashboardSummary.from_model(db_obj, current_user_id=current_user.id)

    @delete(
        operation_id="DeleteDashboard",
        name="dashboards:delete",
        summary="Delete a dashboard (owner only)",
        path="/{dashboard_id:uuid}",
    )
    async def delete_dashboard(
        self,
        dashboards_service: DashboardService,
        current_user: User,
        dashboard_id: UUID,
    ) -> None:
        """Delete an owned dashboard, promoting a new default if needed."""
        dashboard = await self._get_owned(
            dashboards_service, dashboard_id, current_user.id
        )
        was_default = dashboard.is_default
        await dashboards_service.delete(dashboard_id)
        if was_default:
            await self._promote_new_default(dashboards_service, current_user.id)

    @get(
        operation_id="ListDashboardFavorites",
        name="dashboards:list_favorites",
        summary="List the raw favorites pinned to a dashboard",
        path="/{dashboard_id:uuid}/favorites",
    )
    async def list_favorites(
        self,
        dashboards_service: DashboardService,
        favorites_service: UserFavoriteMetricService,
        current_user: User,
        dashboard_id: UUID,
    ) -> list[Favorite]:
        """List a dashboard's favorite items (owner only).

        Used by the star toggle UI to know which targets are already pinned.

        Returns:
            The dashboard's favorites in display order.
        """
        await self._get_owned(dashboards_service, dashboard_id, current_user.id)
        items = await favorites_service.list(
            CollectionFilter("dashboard_id", [dashboard_id]),
            OrderBy(field_name="position"),
        )
        return [
            favorites_service.to_schema(schema_type=Favorite, data=item)
            for item in items
        ]

    @post(
        operation_id="CreateAutoFavorite",
        name="dashboards:create_auto_favorite",
        summary="Pin an auto-collected metric to a dashboard",
        path="/{dashboard_id:uuid}/favorites/auto",
    )
    async def create_auto_favorite(
        self,
        dashboards_service: DashboardService,
        favorites_service: UserFavoriteMetricService,
        current_user: User,
        data: FavoriteAutoCreate,
        dashboard_id: UUID,
    ) -> Favorite:
        """Pin an auto metric by ``(metric_type, chain, token)`` tuple.

        Returns:
            The newly created favorite.
        """
        await self._get_owned(dashboards_service, dashboard_id, current_user.id)
        payload = data.to_dict()
        payload["dashboard_id"] = dashboard_id
        db_obj = await favorites_service.create(payload)
        return favorites_service.to_schema(schema_type=Favorite, data=db_obj)

    @post(
        operation_id="CreateManualFavorite",
        name="dashboards:create_manual_favorite",
        summary="Pin a PROTOCOL_SCORE manual metric to a dashboard",
        path="/{dashboard_id:uuid}/favorites/manual",
    )
    async def create_manual_favorite(
        self,
        dashboards_service: DashboardService,
        favorites_service: UserFavoriteMetricService,
        current_user: User,
        data: FavoriteManualCreate,
        dashboard_id: UUID,
    ) -> Favorite:
        """Pin a manual metric row. The target must be PROTOCOL_SCORE.

        Returns:
            The newly created favorite.
        """
        await self._get_owned(dashboards_service, dashboard_id, current_user.id)
        payload = data.to_dict()
        payload["dashboard_id"] = dashboard_id
        db_obj = await favorites_service.create(payload)
        return favorites_service.to_schema(schema_type=Favorite, data=db_obj)

    @post(
        operation_id="CreateMarketFavorite",
        name="dashboards:create_market_favorite",
        summary="Pin a market_config row to a dashboard",
        path="/{dashboard_id:uuid}/favorites/market",
    )
    async def create_market_favorite(
        self,
        dashboards_service: DashboardService,
        favorites_service: UserFavoriteMetricService,
        current_user: User,
        data: FavoriteMarketCreate,
        dashboard_id: UUID,
    ) -> Favorite:
        """Pin a market by its ``market_config_id``.

        The card value comes from the latest ``MarketScore.final_pd``
        at read time; the service layer rejects disabled markets here
        so a favorite never points at a market the scorer is no longer
        producing fresh PDs for.

        Returns:
            The newly created favorite.
        """
        await self._get_owned(dashboards_service, dashboard_id, current_user.id)
        payload = data.to_dict()
        payload["dashboard_id"] = dashboard_id
        db_obj = await favorites_service.create(payload)
        return favorites_service.to_schema(schema_type=Favorite, data=db_obj)

    @delete(
        operation_id="DeleteFavorite",
        name="dashboards:delete_favorite",
        summary="Unpin a favorite from a dashboard",
        path="/{dashboard_id:uuid}/favorites/{favorite_id:uuid}",
    )
    async def delete_favorite(
        self,
        dashboards_service: DashboardService,
        favorites_service: UserFavoriteMetricService,
        current_user: User,
        dashboard_id: UUID,
        favorite_id: UUID,
    ) -> None:
        """Remove a favorite. The dashboard must be owned by the current user."""
        await self._get_owned(dashboards_service, dashboard_id, current_user.id)
        favorite = await favorites_service.get(favorite_id)
        if favorite.dashboard_id != dashboard_id:
            msg = f"Favorite {favorite_id} not found."
            raise NotFoundException(msg)
        _ = await favorites_service.delete(favorite_id)

    @staticmethod
    async def _get_owned(
        service: DashboardService, dashboard_id: UUID, owner_id: UUID
    ) -> Dashboard:
        """Fetch a dashboard, 404-ing unless the current user owns it.

        Raises:
            NotFoundException: If the dashboard is missing or owned by someone
                else (shared pages are read-only, so non-owners can't mutate).
        """
        dashboard = await service.get_one_or_none(
            Dashboard.id == dashboard_id, Dashboard.owner_id == owner_id
        )
        if dashboard is None:
            msg = f"Dashboard {dashboard_id} not found."
            raise NotFoundException(msg)
        return dashboard

    @staticmethod
    async def _promote_new_default(service: DashboardService, owner_id: UUID) -> None:
        """After deleting the default, make the owner's next page default."""
        stmt = (
            select(Dashboard)
            .where(Dashboard.owner_id == owner_id)
            .order_by(Dashboard.position, Dashboard.created_at)
            .limit(1)
        )
        nxt = (await service.repository.session.execute(stmt)).scalars().first()
        if nxt is not None:
            await service.set_default(dashboard=nxt, owner_id=owner_id)
