# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Alert / integration / history controllers (JSON API + Inertia pages)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from advanced_alchemy.extensions.litestar.providers import (
    FilterConfig,
    create_service_dependencies,
)
from advanced_alchemy.filters import CollectionFilter, LimitOffset
from advanced_alchemy.service import OffsetPagination
from litestar import Controller, Request, delete, get, patch, post
from litestar.exceptions import NotFoundException, PermissionDeniedException
from litestar.params import Dependency, Parameter
from litestar_vite.inertia import InertiaRedirect, flash
from sqlalchemy import or_

from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.alerts.history import parse_history_context
from cert_ra.api.domain.alerts.integrations import (
    WebhookIntegrationConfig,
    parse_integration_config,
)
from cert_ra.api.domain.alerts.rules import parse_rule_config
from cert_ra.api.domain.alerts.schemas import (
    Alert,
    AlertCreate,
    AlertHistory,
    AlertHistoryListPage,
    AlertIntegration,
    AlertIntegrationCreate,
    AlertIntegrationListPage,
    AlertIntegrationUpdate,
    AlertListPage,
    AlertOverrideUpdate,
    AlertUpdate,
)
from cert_ra.api.domain.alerts.services import (
    AlertHistoryService,
    AlertIntegrationService,
    AlertService,
    TeamAlertOverrideService,
)
from cert_ra.api.domain.alerts.targets import parse_target_config
from cert_ra.db.models import (
    Alert as AlertModel,
    AlertHistory as AlertHistoryModel,
    AlertIntegration as AlertIntegrationModel,
    TeamRoles,
    User,
)
from cert_ra.types import (
    AlertHistoryStatus,  # noqa: TC001 — runtime import needed for Litestar query-param parsing
    AlertRuleKind,  # noqa: TC001
    AlertSeverity,  # noqa: TC001
    AlertTargetKind,  # noqa: TC001
)

if TYPE_CHECKING:
    from advanced_alchemy.filters import FilterTypes

__all__ = (
    "AlertApiController",
    "AlertIntegrationApiController",
    "AlertPageController",
)


_ALERT_BASE_FILTERS: FilterConfig = {
    "id_filter": UUID,
    "created_at": True,
    "updated_at": True,
    "sort_field": "created_at",
    "sort_order": "desc",
    "pagination_type": "limit_offset",
    "pagination_size": 50,
}

_HISTORY_BASE_FILTERS: FilterConfig = {
    "id_filter": UUID,
    "created_at": True,
    "sort_field": "evaluated_at",
    "sort_order": "desc",
    "pagination_type": "limit_offset",
    "pagination_size": 50,
}


def _user_team_ids(user: User) -> list[UUID]:
    """Extract team IDs the user belongs to from the eagerly-loaded relationship."""
    return [membership.team_id for membership in user.teams]


def _is_team_editor(user: User, team_id: UUID) -> bool:
    """Return True if ``user`` is an editor/admin/owner of ``team_id``.

    Decided synchronously from the eagerly-loaded ``user.teams`` relationship.
    """
    if user.is_superuser:
        return True
    return any(
        m.team_id == team_id
        and (m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR) or m.is_owner)
        for m in user.teams
    )


def _is_operator_editor(user: User) -> bool:
    """Return True if ``user`` is an editor/admin/owner of the operator team.

    Decided synchronously from the eagerly-loaded ``user.teams.team`` chain.
    """
    if user.is_superuser:
        return True
    return any(
        m.team.is_operator
        and (m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR) or m.is_owner)
        for m in user.teams
    )


def _assert_can_write_alert(user: User, alert: AlertModel) -> None:
    """Raise ``PermissionDeniedException`` if the user can't mutate ``alert``.

    Templates require operator-team editor access; team-owned alerts require
    editor access for that team.
    """
    if alert.is_template:
        allowed = _is_operator_editor(user)
        detail = "Operator team editor access is required to mutate templates."
    else:
        # Non-template rows always have a team_id (DB XOR check enforces this).
        allowed = alert.team_id is not None and _is_team_editor(user, alert.team_id)
        detail = "Team editor access is required to mutate this alert."
    if not allowed:
        raise PermissionDeniedException(detail=detail)


def _assert_can_write_integration(
    user: User,
    integration: AlertIntegrationModel,
) -> None:
    """Raise ``PermissionDeniedException`` if the user can't mutate ``integration``."""
    if not _is_team_editor(user, integration.team_id):
        raise PermissionDeniedException(
            detail="Team editor access is required to mutate this integration.",
        )


def _limit_offset(filters: list[FilterTypes]) -> tuple[int, int]:
    """Pull the ``LimitOffset`` values out of a filter list, with defaults."""
    for f in filters:
        if isinstance(f, LimitOffset):
            return f.limit, f.offset
    return 50, 0


def _alert_to_schema(alert: AlertModel) -> Alert:
    """Convert an ``Alert`` row to its response schema with typed JSONB columns."""
    return Alert(
        id=alert.id,
        team_id=alert.team_id,
        is_template=alert.is_template,
        name=alert.name,
        description=alert.description,
        target_kind=alert.target_kind,
        target_config=parse_target_config(alert.target_kind, alert.target_config),
        rule_kind=alert.rule_kind,
        rule_config=parse_rule_config(alert.rule_kind, alert.rule_config),
        severity=alert.severity,
        is_enabled=alert.is_enabled,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
        created_by=alert.created_by,
        updated_by=alert.updated_by,
    )


_SECRET_PLACEHOLDER = "***"  # noqa: S105 — public placeholder, not a real secret


def _integration_to_schema(integration: AlertIntegrationModel) -> AlertIntegration:
    """Convert an ``AlertIntegration`` row to its response schema with typed ``config``.

    Sensitive fields (today: ``WebhookIntegrationConfig.secret``) are redacted
    before returning. The dispatcher is the only consumer that needs the
    cleartext value — everywhere else sees ``"***"``.
    """
    typed_config = parse_integration_config(integration.kind, integration.config)
    if isinstance(typed_config, WebhookIntegrationConfig):
        typed_config = WebhookIntegrationConfig(
            url=typed_config.url,
            secret=_SECRET_PLACEHOLDER,
            headers=typed_config.headers,
        )
    return AlertIntegration(
        id=integration.id,
        team_id=integration.team_id,
        kind=integration.kind,
        name=integration.name,
        config=typed_config,
        is_primary=integration.is_primary,
        is_active=integration.is_active,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
        created_by=integration.created_by,
        updated_by=integration.updated_by,
    )


def _history_to_schema(history: AlertHistoryModel) -> AlertHistory:
    """Convert an ``AlertHistory`` row to its response schema with typed ``context``."""
    return AlertHistory(
        id=history.id,
        alert_id=history.alert_id,
        team_id=history.team_id,
        status=history.status,
        metric_value=history.metric_value,
        threshold=history.threshold,
        message=history.message,
        context=parse_history_context(history.context),
        evaluated_at=history.evaluated_at,
        created_at=history.created_at,
        updated_at=history.updated_at,
    )


def _build_alert_filters(
    base_filters: list[FilterTypes],
    *,
    target_kind: AlertTargetKind | None,
    rule_kind: AlertRuleKind | None,
    severity: AlertSeverity | None,
    is_template: bool | None,
    is_enabled: bool | None,
) -> list[FilterTypes]:
    """Append strict-equality filters for the alerts list endpoint."""
    extra: list[FilterTypes] = []
    if target_kind is not None:
        extra.append(CollectionFilter("target_kind", [target_kind]))
    if rule_kind is not None:
        extra.append(CollectionFilter("rule_kind", [rule_kind]))
    if severity is not None:
        extra.append(CollectionFilter("severity", [severity]))
    if is_template is not None:
        extra.append(CollectionFilter("is_template", [is_template]))
    if is_enabled is not None:
        extra.append(CollectionFilter("is_enabled", [is_enabled]))
    return [*base_filters, *extra]


class AlertApiController(Controller):
    """Alerts JSON read API.

    Visibility rule: a user sees an alert if it is an operator template
    (``is_template = true``) OR if the alert's ``team_id`` is one of the user's
    teams. Superusers see everything.
    """

    path = "/api/alerts"
    tags = ["Alerts"]  # noqa: RUF012
    guards = [requires_active_user]  # noqa: RUF012
    dependencies = create_service_dependencies(
        AlertService,
        key="alerts_service",
        filters=_ALERT_BASE_FILTERS,
    )
    signature_namespace = {  # noqa: RUF012
        "AlertService": AlertService,
        "AlertHistoryService": AlertHistoryService,
    }

    @get(
        operation_id="ListAlerts",
        name="alerts:list",
        summary="List alerts visible to the current user",
        path="/",
    )
    async def list_alerts(
        self,
        alerts_service: AlertService,
        current_user: User,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        target_kind: AlertTargetKind | None = None,
        rule_kind: AlertRuleKind | None = None,
        severity: AlertSeverity | None = None,
        is_template: bool | None = None,  # noqa: FBT001
        is_enabled: bool | None = None,  # noqa: FBT001
    ) -> OffsetPagination[Alert]:
        """List alerts visible to the current user.

        Returns:
            Paginated list of alerts (templates + the user's team alerts).
        """
        all_filters = _build_alert_filters(
            list(filters),
            target_kind=target_kind,
            rule_kind=rule_kind,
            severity=severity,
            is_template=is_template,
            is_enabled=is_enabled,
        )
        if not current_user.is_superuser:
            team_ids = _user_team_ids(current_user)
            visibility = (
                or_(
                    AlertModel.is_template.is_(True),
                    AlertModel.team_id.in_(team_ids),
                )
                if team_ids
                else AlertModel.is_template.is_(True)
            )
            all_filters.append(visibility)  # type: ignore[arg-type]
        results, total = await alerts_service.list_and_count(*all_filters)
        limit, offset = _limit_offset(all_filters)
        return OffsetPagination[Alert](
            items=[_alert_to_schema(a) for a in results],
            total=total,
            limit=limit,
            offset=offset,
        )

    @get(
        operation_id="GetAlert",
        name="alerts:get",
        summary="Get an alert by id",
        path="/{alert_id:uuid}",
    )
    async def get_alert(
        self,
        alerts_service: AlertService,
        current_user: User,
        alert_id: Annotated[
            UUID, Parameter(title="Alert ID", description="Alert to fetch.")
        ],
    ) -> Alert:
        """Get one alert by id (must be visible to the current user).

        Returns:
            The requested alert.
        """
        alert = await alerts_service.get(alert_id)
        if not _is_visible(alert, current_user):
            # ``get`` already raises NotFound on missing rows; reaching here
            # means the row exists but the user can't see it. Raising
            # NotFound preserves enumeration safety.
            raise NotFoundException(detail="Alert not found.")
        return _alert_to_schema(alert)

    @get(
        operation_id="ListAlertHistory",
        name="alerts:history",
        summary="List history events for an alert",
        path="/{alert_id:uuid}/history",
        dependencies=create_service_dependencies(
            AlertHistoryService,
            key="history_service",
            filters=_HISTORY_BASE_FILTERS,
        ),
    )
    async def list_alert_history(
        self,
        alerts_service: AlertService,
        history_service: AlertHistoryService,
        current_user: User,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        alert_id: Annotated[
            UUID, Parameter(title="Alert ID", description="Alert to scope to.")
        ],
        status: AlertHistoryStatus | None = None,
    ) -> OffsetPagination[AlertHistory]:
        """List history rows for one alert (visibility-checked).

        Returns:
            Paginated history events for the requested alert.
        """
        alert = await alerts_service.get(alert_id)
        if not _is_visible(alert, current_user):
            raise NotFoundException(detail="Alert not found.")
        all_filters: list[FilterTypes] = [
            *filters,
            CollectionFilter("alert_id", [alert_id]),
        ]
        if status is not None:
            all_filters.append(CollectionFilter("status", [status]))
        results, total = await history_service.list_and_count(*all_filters)
        limit, offset = _limit_offset(all_filters)
        return OffsetPagination[AlertHistory](
            items=[_history_to_schema(h) for h in results],
            total=total,
            limit=limit,
            offset=offset,
        )

    @post(
        operation_id="CreateAlert",
        name="alerts:create",
        summary="Create a new alert (template or team-owned)",
        path="/",
    )
    async def create_alert(
        self,
        alerts_service: AlertService,
        current_user: User,
        data: AlertCreate,
    ) -> Alert:
        """Create a new alert.

        Permission rules:

        - ``is_template=True`` requires operator-team editor access. ``team_id``
          must not be supplied (templates carry no team).
        - ``is_template=False`` (default) creates a team-owned alert. Caller
          must be editor of one of their teams; the controller picks the
          first team the caller can edit. Future: explicit team_id in body.

        Returns:
            The newly created alert.
        """
        payload = data.to_dict()
        payload["created_by"] = current_user.id
        payload["updated_by"] = current_user.id
        if data.is_template:
            if not _is_operator_editor(current_user):
                raise PermissionDeniedException(
                    detail="Operator team editor access is required to create templates."
                )
            payload["team_id"] = None
        else:
            # Find a team the caller can edit. For Phase 3 we pick the first
            # eligible membership; the frontend will eventually pass an
            # explicit team_id.
            target_team_id = next(
                (
                    m.team_id
                    for m in current_user.teams
                    if (m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR) or m.is_owner)
                    and not m.team.is_operator
                ),
                None,
            )
            if target_team_id is None:
                raise PermissionDeniedException(
                    detail="You must be an editor of a team to create an alert."
                )
            payload["team_id"] = target_team_id
        db_obj = await alerts_service.create(payload)
        return _alert_to_schema(db_obj)

    @patch(
        operation_id="UpdateAlert",
        name="alerts:update",
        summary="Update an alert",
        path="/{alert_id:uuid}",
    )
    async def update_alert(
        self,
        alerts_service: AlertService,
        current_user: User,
        data: AlertUpdate,
        alert_id: Annotated[
            UUID, Parameter(title="Alert ID", description="Alert to update.")
        ],
    ) -> Alert:
        """Update an alert (permission depends on whether it's a template).

        Returns:
            The updated alert.
        """
        existing = await alerts_service.get(alert_id)
        _assert_can_write_alert(current_user, existing)
        payload = data.to_dict()
        payload["updated_by"] = current_user.id
        db_obj = await alerts_service.update(item_id=alert_id, data=payload)
        return _alert_to_schema(db_obj)

    @delete(
        operation_id="DeleteAlert",
        name="alerts:delete",
        summary="Delete an alert",
        path="/{alert_id:uuid}",
    )
    async def delete_alert(
        self,
        alerts_service: AlertService,
        current_user: User,
        alert_id: Annotated[
            UUID, Parameter(title="Alert ID", description="Alert to delete.")
        ],
    ) -> None:
        """Hard-delete an alert (permission depends on whether it's a template)."""
        existing = await alerts_service.get(alert_id)
        _assert_can_write_alert(current_user, existing)
        _ = await alerts_service.delete(alert_id)

    @patch(
        operation_id="UpsertAlertOverride",
        name="alerts:override",
        summary="Toggle a template (or override its integration) for the caller's team",
        path="/{alert_id:uuid}/override",
        dependencies=create_service_dependencies(
            TeamAlertOverrideService,
            key="overrides_service",
        ),
    )
    async def upsert_override(
        self,
        alerts_service: AlertService,
        overrides_service: TeamAlertOverrideService,
        current_user: User,
        data: AlertOverrideUpdate,
        alert_id: Annotated[
            UUID, Parameter(title="Alert ID", description="Template alert to override.")
        ],
    ) -> None:
        """Upsert a per-team override row.

        Only operator templates can be overridden — overriding a team-owned
        alert is rejected because the team can edit the alert directly.
        """
        alert = await alerts_service.get(alert_id)
        if not alert.is_template:
            raise PermissionDeniedException(
                detail="Only operator templates can be overridden; "
                "team-owned alerts are mutated via PATCH /api/alerts/{id}.",
            )
        if not _is_team_editor(current_user, data.team_id):
            raise PermissionDeniedException(
                detail="Team editor access is required to toggle this template.",
            )
        existing = await overrides_service.get_one_or_none(
            team_id=data.team_id, alert_id=alert_id
        )
        if existing is None:
            await overrides_service.create(
                {
                    "team_id": data.team_id,
                    "alert_id": alert_id,
                    "is_enabled": data.is_enabled,
                    "integration_id": data.integration_id,
                }
            )
        else:
            await overrides_service.update(
                item_id=existing.id,
                data={
                    "is_enabled": data.is_enabled,
                    "integration_id": data.integration_id,
                },
            )


class AlertIntegrationApiController(Controller):
    """Alert integrations JSON read API.

    Returns integrations belonging to teams the current user is a member of.
    """

    path = "/api/alert-integrations"
    tags = ["Alerts"]  # noqa: RUF012
    guards = [requires_active_user]  # noqa: RUF012
    dependencies = create_service_dependencies(
        AlertIntegrationService,
        key="integrations_service",
        filters=_ALERT_BASE_FILTERS,
    )
    signature_namespace = {  # noqa: RUF012
        "AlertIntegrationService": AlertIntegrationService,
    }

    @get(
        operation_id="ListAlertIntegrations",
        name="alert_integrations:list",
        summary="List integrations for the current user's teams",
        path="/",
    )
    async def list_integrations(
        self,
        integrations_service: AlertIntegrationService,
        current_user: User,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> OffsetPagination[AlertIntegration]:
        """List integrations for any of the current user's teams.

        Returns:
            Paginated list of integrations.
        """
        all_filters = list(filters)
        limit, offset = _limit_offset(all_filters)
        if not current_user.is_superuser:
            team_ids = _user_team_ids(current_user)
            if not team_ids:
                # No team membership → no integrations are visible.
                return OffsetPagination[AlertIntegration](
                    items=[], total=0, limit=limit, offset=offset
                )
            all_filters.append(CollectionFilter("team_id", team_ids))
        results, total = await integrations_service.list_and_count(*all_filters)
        return OffsetPagination[AlertIntegration](
            items=[_integration_to_schema(i) for i in results],
            total=total,
            limit=limit,
            offset=offset,
        )

    @get(
        operation_id="GetAlertIntegration",
        name="alert_integrations:get",
        summary="Get an integration by id",
        path="/{integration_id:uuid}",
    )
    async def get_integration(
        self,
        integrations_service: AlertIntegrationService,
        current_user: User,
        integration_id: Annotated[
            UUID,
            Parameter(title="Integration ID", description="Integration to fetch."),
        ],
    ) -> AlertIntegration:
        """Get one integration by id (must belong to one of the user's teams).

        Returns:
            The requested integration.
        """
        integration = await integrations_service.get(integration_id)
        if not current_user.is_superuser and integration.team_id not in _user_team_ids(
            current_user
        ):
            raise NotFoundException(detail="Integration not found.")
        return _integration_to_schema(integration)

    @post(
        operation_id="CreateAlertIntegration",
        name="alert_integrations:create",
        summary="Create a new integration for one of the caller's teams",
        path="/",
    )
    async def create_integration(
        self,
        integrations_service: AlertIntegrationService,
        current_user: User,
        data: AlertIntegrationCreate,
    ) -> AlertIntegration:
        """Create a new integration. Caller must be editor of a team.

        Phase 3: the controller picks the first team the caller can edit.
        Future: explicit team_id in body.

        Returns:
            The newly created integration.
        """
        target_team_id = next(
            (
                m.team_id
                for m in current_user.teams
                if m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR) or m.is_owner
            ),
            None,
        )
        if target_team_id is None:
            raise PermissionDeniedException(
                detail="You must be an editor of a team to create an integration."
            )
        payload = data.to_dict()
        payload["team_id"] = target_team_id
        payload["created_by"] = current_user.id
        payload["updated_by"] = current_user.id
        db_obj = await integrations_service.create(payload)
        return _integration_to_schema(db_obj)

    @patch(
        operation_id="UpdateAlertIntegration",
        name="alert_integrations:update",
        summary="Update an integration",
        path="/{integration_id:uuid}",
    )
    async def update_integration(
        self,
        integrations_service: AlertIntegrationService,
        current_user: User,
        data: AlertIntegrationUpdate,
        integration_id: Annotated[
            UUID,
            Parameter(title="Integration ID", description="Integration to update."),
        ],
    ) -> AlertIntegration:
        """Update an integration (config / name / flags). ``kind`` is immutable.

        Returns:
            The updated integration.
        """
        existing = await integrations_service.get(integration_id)
        _assert_can_write_integration(current_user, existing)
        payload = data.to_dict()
        payload["updated_by"] = current_user.id
        if "config" in payload:
            # Service-layer validator needs the existing kind to choose the
            # right config schema. ``kind`` is immutable; the service drops
            # it from the persisted payload after validating.
            payload["kind"] = existing.kind
        db_obj = await integrations_service.update(item_id=integration_id, data=payload)
        return _integration_to_schema(db_obj)

    @delete(
        operation_id="DeleteAlertIntegration",
        name="alert_integrations:delete",
        summary="Delete an integration",
        path="/{integration_id:uuid}",
    )
    async def delete_integration(
        self,
        integrations_service: AlertIntegrationService,
        current_user: User,
        integration_id: Annotated[
            UUID,
            Parameter(title="Integration ID", description="Integration to delete."),
        ],
    ) -> None:
        """Hard-delete an integration."""
        existing = await integrations_service.get(integration_id)
        _assert_can_write_integration(current_user, existing)
        _ = await integrations_service.delete(integration_id)


def _is_visible(alert: AlertModel, user: User) -> bool:
    """A user can see an alert if it's a template, their team owns it, or they're a superuser."""
    if user.is_superuser:
        return True
    if alert.is_template:
        return True
    return alert.team_id in _user_team_ids(user)


class AlertPageController(Controller):
    """Inertia pages for the alerting subsystem.

    These page controllers serve the initial page render with typed props;
    the React side hydrates further interactions through the JSON API
    endpoints in ``AlertApiController`` / ``AlertIntegrationApiController``.
    """

    tags = ["Alerts"]  # noqa: RUF012
    guards = [requires_active_user]  # noqa: RUF012
    signature_namespace = {  # noqa: RUF012
        "AlertService": AlertService,
        "AlertIntegrationService": AlertIntegrationService,
        "AlertHistoryService": AlertHistoryService,
    }

    @get(
        component="alerts/list",
        name="alerts",
        operation_id="AlertsListPage",
        path="/alerts",
        dependencies=create_service_dependencies(AlertService, key="alerts_service"),
    )
    async def list_page(
        self,
        alerts_service: AlertService,
        current_user: User,
    ) -> AlertListPage:
        """Initial render for the alerts list page.

        Loads alerts visible to the current user (templates + their team
        alerts). The React side can re-fetch with filters via the JSON API.

        Returns:
            Page props with alerts and the caller's role flags.
        """
        if current_user.is_superuser:
            results, total = await alerts_service.list_and_count()
        else:
            team_ids = _user_team_ids(current_user)
            visibility = (
                or_(
                    AlertModel.is_template.is_(True),
                    AlertModel.team_id.in_(team_ids),
                )
                if team_ids
                else AlertModel.is_template.is_(True)
            )
            results, total = await alerts_service.list_and_count(visibility)
        return AlertListPage(
            items=[_alert_to_schema(a) for a in results],
            total=total,
            is_team_editor=any(
                m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR) or m.is_owner
                for m in current_user.teams
            ),
            is_operator_editor=_is_operator_editor(current_user),
        )

    @get(
        component="alerts/history",
        name="alerts.history",
        operation_id="AlertsHistoryPage",
        path="/alerts/{alert_id:uuid}/history",
        dependencies={
            **create_service_dependencies(AlertService, key="alerts_service"),
            **create_service_dependencies(AlertHistoryService, key="history_service"),
        },
    )
    async def history_page(
        self,
        alerts_service: AlertService,
        current_user: User,
        alert_id: Annotated[
            UUID, Parameter(title="Alert ID", description="Alert to view.")
        ],
        history_service: AlertHistoryService,
    ) -> AlertHistoryListPage:
        """Initial render for the per-alert history page.

        Returns:
            Page props with the alert's metadata and its history rows.
        """
        alert = await alerts_service.get(alert_id)
        if not _is_visible(alert, current_user):
            raise NotFoundException(detail="Alert not found.")
        history_results, history_total = await history_service.list_and_count(
            CollectionFilter("alert_id", [alert_id])
        )
        return AlertHistoryListPage(
            alert=_alert_to_schema(alert),
            items=[_history_to_schema(h) for h in history_results],
            total=history_total,
        )

    @get(
        component="alerts/integrations",
        name="alerts.integrations",
        operation_id="AlertsIntegrationsPage",
        path="/alerts/integrations",
        dependencies=create_service_dependencies(
            AlertIntegrationService,
            key="integrations_service",
        ),
    )
    async def integrations_page(
        self,
        integrations_service: AlertIntegrationService,
        current_user: User,
    ) -> AlertIntegrationListPage:
        """Initial render for the integrations management page.

        Returns:
            Page props with the integrations belonging to the user's teams.
        """
        if current_user.is_superuser:
            results, total = await integrations_service.list_and_count()
        else:
            team_ids = _user_team_ids(current_user)
            if not team_ids:
                return AlertIntegrationListPage(
                    items=[],
                    total=0,
                    is_team_editor=False,
                )
            results, total = await integrations_service.list_and_count(
                CollectionFilter("team_id", team_ids)
            )
        return AlertIntegrationListPage(
            items=[_integration_to_schema(i) for i in results],
            total=total,
            is_team_editor=any(
                m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR) or m.is_owner
                for m in current_user.teams
            ),
        )

    @post(
        name="alerts.create",
        operation_id="AlertsCreate",
        path="/alerts",
        dependencies=create_service_dependencies(AlertService, key="alerts_service"),
    )
    async def create_page(
        self,
        request: Request,
        alerts_service: AlertService,
        current_user: User,
        data: AlertCreate,
    ) -> InertiaRedirect:
        """Create an alert from the Inertia form and redirect back to the list.

        Operator editors may set ``is_template=True``; regular team editors
        create team-owned alerts. Permission logic mirrors
        ``AlertApiController.create_alert``.

        Returns:
            Inertia redirect to the alerts list page.
        """
        payload = data.to_dict()
        payload["created_by"] = current_user.id
        payload["updated_by"] = current_user.id
        if data.is_template:
            if not _is_operator_editor(current_user):
                raise PermissionDeniedException(
                    detail="Operator team editor access is required to create templates.",
                )
            payload["team_id"] = None
        else:
            target_team_id = next(
                (
                    m.team_id
                    for m in current_user.teams
                    if (m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR) or m.is_owner)
                    and not m.team.is_operator
                ),
                None,
            )
            if target_team_id is None:
                raise PermissionDeniedException(
                    detail="You must be an editor of a team to create an alert.",
                )
            payload["team_id"] = target_team_id
        db_obj = await alerts_service.create(payload)
        flash(request, f'Created alert "{db_obj.name}".', category="info")
        return InertiaRedirect(request, request.url_for("alerts"))
