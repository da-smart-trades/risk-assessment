# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Admin controller for the operator-curated market_config table.

Superuser-only — only the operator team manages which protocols the
collector and scorer talk to. Each mutation is recorded in the audit
log so an operator can later see who added/edited/disabled which
protocol and when.

Inertia pages:

* ``GET /admin/market-config/`` — list of every market_config row
  (one per protocol).
* ``GET /admin/market-config/create`` — empty form (protocol input).
* ``GET /admin/market-config/{id}/`` — populated edit form.

Mutations (Inertia redirects on success):

* ``POST   /admin/market-config/`` — create a protocol row.
* ``PATCH  /admin/market-config/{id}/`` — toggle ``enabled``.
* ``DELETE /admin/market-config/{id}/`` — cascade-deletes the
  protocol's snapshots / scores / market favorites via the
  ``ON DELETE CASCADE`` FKs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from advanced_alchemy.exceptions import RepositoryError
from advanced_alchemy.extensions.litestar.providers import create_service_dependencies
from litestar import Controller, Request, delete, get, patch, post
from litestar.di import Provide
from litestar.exceptions import ValidationException
from litestar.params import Dependency, Parameter
from litestar_vite.inertia import InertiaRedirect, flash

from cert_ra.api.domain.accounts.guards import requires_superuser
from cert_ra.api.domain.admin.dependencies import provide_audit_service
from cert_ra.api.domain.market_config.schemas import (
    AdminMarketConfigCreatePage,
    AdminMarketConfigEditPage,
    AdminMarketConfigListPage,
    MarketConfig as MarketConfigSchema,
    MarketConfigCreate,
    MarketConfigUpdate,
)
from cert_ra.api.domain.market_config.services import MarketConfigService
from cert_ra.db.models import AuditAction, MarketConfig, User
from cert_ra.types import ProtocolType

if TYPE_CHECKING:
    from advanced_alchemy.filters import FilterTypes

    from cert_ra.api.domain.admin.services import AuditLogService

__all__ = ("AdminMarketConfigController",)


def _protocol_options() -> list[str]:
    """``ProtocolType`` values offered for the assurance-mapping dropdown."""
    return [p.value for p in ProtocolType]


def _to_schema(row: MarketConfig) -> MarketConfigSchema:
    return MarketConfigSchema(
        id=row.id,
        protocol=row.protocol,
        enabled=row.enabled,
        assurance_protocol=row.assurance_protocol,
        created_at=row.created_at,
        updated_at=row.updated_at,
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


class AdminMarketConfigController(Controller):
    """Superuser-only CRUD on the market_config table."""

    tags = ["Admin - Market Config"]  # noqa: RUF012
    path = "/admin/market-config"
    guards = [requires_superuser]  # noqa: RUF012
    dependencies = create_service_dependencies(
        MarketConfigService,
        key="market_config_service",
        filters={
            "id_filter": UUID,
            "search": "protocol",
            "pagination_type": "limit_offset",
            "pagination_size": 50,
            "created_at": True,
            "updated_at": True,
            "sort_field": "protocol",
            "sort_order": "asc",
        },
    ) | {
        "audit_service": Provide(provide_audit_service),
    }
    signature_namespace = {  # noqa: RUF012
        "MarketConfigService": MarketConfigService,
        "MarketConfigCreate": MarketConfigCreate,
        "MarketConfigUpdate": MarketConfigUpdate,
    }

    # -----------------------------------------------------------------
    # Pages
    # -----------------------------------------------------------------

    @get(
        component="admin/market-config/list",
        name="admin.market_config.list",
        operation_id="AdminMarketConfigList",
        path="/",
    )
    async def list_page(
        self,
        market_config_service: MarketConfigService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> AdminMarketConfigListPage:
        """Render the admin list page."""
        rows, total = await market_config_service.list_and_count(*filters)
        return AdminMarketConfigListPage(
            markets=[_to_schema(r) for r in rows], total=total
        )

    @get(
        component="admin/market-config/create",
        name="admin.market_config.create_page",
        operation_id="AdminMarketConfigCreatePage",
        path="/create",
    )
    async def create_page(self) -> AdminMarketConfigCreatePage:
        """Render the empty create form."""
        return AdminMarketConfigCreatePage(protocol_options=_protocol_options())

    @get(
        component="admin/market-config/edit",
        name="admin.market_config.edit_page",
        operation_id="AdminMarketConfigEditPage",
        path="/{market_config_id:uuid}/",
    )
    async def edit_page(
        self,
        market_config_service: MarketConfigService,
        market_config_id: Annotated[UUID, Parameter(title="Market config ID")],
    ) -> AdminMarketConfigEditPage:
        """Render the edit form populated with the current row's values."""
        row = await market_config_service.get(market_config_id)
        return AdminMarketConfigEditPage(
            market=_to_schema(row), protocol_options=_protocol_options()
        )

    # -----------------------------------------------------------------
    # Mutations
    # -----------------------------------------------------------------

    @post(
        name="admin.market_config.create",
        operation_id="AdminMarketConfigCreate",
        path="/",
    )
    async def create(
        self,
        request: Request,
        market_config_service: MarketConfigService,
        audit_service: AuditLogService,
        current_user: User,
        data: MarketConfigCreate,
    ) -> InertiaRedirect:
        """Create a market config row.

        Returns:
            Redirect to the admin list page on success.
        """
        try:
            payload = data.to_dict()
            payload["created_by"] = current_user.id
            payload["updated_by"] = current_user.id
            row = await market_config_service.create(payload)
        except RepositoryError as exc:
            raise ValidationException(detail=str(exc)) from exc

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.MARKET_CONFIG_CREATED,
            target_type="market_config",
            target_id=row.id,
            target_label=row.protocol,
            details={
                "protocol": row.protocol,
                "enabled": row.enabled,
            },
            ip_address=request.client.host if request.client else None,
        )
        flash(request, f"Added protocol {row.protocol}.", category="success")
        return InertiaRedirect(request, request.url_for("admin.market_config.list"))

    @patch(
        name="admin.market_config.update",
        operation_id="AdminMarketConfigUpdate",
        path="/{market_config_id:uuid}/",
    )
    async def update(
        self,
        request: Request,
        market_config_service: MarketConfigService,
        audit_service: AuditLogService,
        current_user: User,
        market_config_id: Annotated[UUID, Parameter(title="Market config ID")],
        data: MarketConfigUpdate,
    ) -> InertiaRedirect:
        """Toggle the ``enabled`` flag on an existing protocol row.

        Returns:
            Redirect to the edit page.
        """
        try:
            payload = data.to_dict()
            payload["updated_by"] = current_user.id
            row = await market_config_service.update(
                item_id=market_config_id, data=payload
            )
        except RepositoryError as exc:
            raise ValidationException(detail=str(exc)) from exc

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.MARKET_CONFIG_UPDATED,
            target_type="market_config",
            target_id=row.id,
            target_label=row.protocol,
            details=data.to_dict(),
            ip_address=request.client.host if request.client else None,
        )
        flash(request, f"Updated protocol {row.protocol}.", category="info")
        return InertiaRedirect(
            request,
            request.url_for("admin.market_config.edit_page", market_config_id=row.id),
        )

    @delete(
        name="admin.market_config.delete",
        operation_id="AdminMarketConfigDelete",
        path="/{market_config_id:uuid}/",
        status_code=303,
    )
    async def delete(
        self,
        request: Request,
        market_config_service: MarketConfigService,
        audit_service: AuditLogService,
        current_user: User,
        market_config_id: Annotated[UUID, Parameter(title="Market config ID")],
    ) -> InertiaRedirect:
        """Delete a market config row.

        Returns:
            Redirect to the admin list page.
        """
        row = await market_config_service.get(market_config_id)
        protocol = row.protocol
        await market_config_service.delete(market_config_id)
        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.MARKET_CONFIG_DELETED,
            target_type="market_config",
            target_id=market_config_id,
            target_label=protocol,
            ip_address=request.client.host if request.client else None,
        )
        flash(request, f"Deleted protocol {protocol}.", category="warning")
        return InertiaRedirect(request, request.url_for("admin.market_config.list"))
