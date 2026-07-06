# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING

from litestar.exceptions import PermissionDeniedException
from sqlalchemy import select

from cert_ra.api.config import alchemy
from cert_ra.db.models import Team, TeamMember, TeamRoles

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection
    from litestar.handlers.base import BaseRouteHandler
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "requires_operator_admin",
    "requires_operator_editor",
    "requires_operator_member",
    "requires_operator_tenant_admin",
    "requires_team_admin",
    "requires_team_editor",
    "requires_team_membership",
    "requires_team_ownership",
]


def _has_superuser_role(connection: ASGIConnection) -> bool:
    """Return True if the user is a superuser by flag or assigned role."""
    if connection.user.is_superuser:
        return True
    return any(
        assigned_role.role.name == "Superuser"
        for assigned_role in connection.user.roles
    )


async def _operator_membership_status(
    connection: ASGIConnection,
) -> tuple[bool, bool, bool]:
    """Return ``(is_member, is_editor_or_admin, is_admin_or_owner)`` for the operator team.

    The connection.user object may be detached from its loading session by the
    time guards run, which makes ``user.teams[*].team.is_operator`` traversal
    unsafe in a sync context. We fetch the membership state from a fresh
    async session instead.
    """
    user_id = connection.user.id
    session: AsyncSession = alchemy.provide_session(
        connection.app.state, connection.scope
    )
    rows = (
        await session.execute(
            select(TeamMember.role, TeamMember.is_owner)
            .join(Team, TeamMember.team_id == Team.id)
            .where(TeamMember.user_id == user_id, Team.is_operator.is_(True))
        )
    ).all()
    is_member = bool(rows)
    is_editor_or_admin = any(
        row.is_owner
        or row.role
        in (TeamRoles.ADMIN, TeamRoles.EDITOR, TeamRoles.OPERATOR_TENANT_ADMIN)
        for row in rows
    )
    # operator_tenant_admin (and operator owners) are write-capable;
    # operator_support is read-only (PR-8, Control 2).
    is_admin_or_owner = any(
        row.is_owner or row.role in (TeamRoles.ADMIN, TeamRoles.OPERATOR_TENANT_ADMIN)
        for row in rows
    )
    return is_member, is_editor_or_admin, is_admin_or_owner


def requires_team_membership(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Verify the connection user is a member of the team.

    Args:
        connection: HTTP connection object.
        _: Route handler (unused).

    Raises:
        PermissionDeniedException: If user is not a team member.
    """
    team_slug = connection.path_params["team_slug"]
    has_system_role = any(
        assigned_role.role.name == "Superuser"
        for assigned_role in connection.user.roles
    )
    has_team_role = any(
        membership.team.slug == team_slug for membership in connection.user.teams
    )
    if connection.user.is_superuser or has_system_role or has_team_role:
        return
    raise PermissionDeniedException(detail="You can't access this team")


def requires_team_admin(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Verify the connection user is a team admin.

    Args:
        connection: HTTP connection object.
        _: Route handler (unused).

    Raises:
        PermissionDeniedException: If user is not a team admin.
    """
    team_slug = connection.path_params["team_slug"]
    has_system_role = any(
        assigned_role.role.name == "Superuser"
        for assigned_role in connection.user.roles
    )
    has_team_role = any(
        membership.team.slug == team_slug and membership.role == TeamRoles.ADMIN
        for membership in connection.user.teams
    )
    if connection.user.is_superuser or has_system_role or has_team_role:
        return
    raise PermissionDeniedException(
        detail="Admin access is required to access this resource"
    )


def requires_team_editor(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Verify the connection user is a team admin or editor.

    Use this guard for write actions that an EDITOR-level team member should be
    able to perform (e.g. creating / editing / deleting alerts for their team).
    Plain MEMBER role is rejected.

    Args:
        connection: HTTP connection object.
        _: Route handler (unused).

    Raises:
        PermissionDeniedException: If user is not an admin or editor of the team.
    """
    team_slug = connection.path_params["team_slug"]
    has_system_role = any(
        assigned_role.role.name == "Superuser"
        for assigned_role in connection.user.roles
    )
    has_team_role = any(
        membership.team.slug == team_slug
        and (
            membership.role in (TeamRoles.ADMIN, TeamRoles.EDITOR)
            or membership.is_owner
        )
        for membership in connection.user.teams
    )
    if connection.user.is_superuser or has_system_role or has_team_role:
        return
    raise PermissionDeniedException(
        detail="Editor or admin access is required to access this resource"
    )


def requires_team_ownership(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Verify that the connection user is the team owner.

    Args:
        connection: HTTP connection object.
        _: Route handler (unused).

    Raises:
        PermissionDeniedException: If user is not the team owner.
    """
    team_slug = connection.path_params["team_slug"]
    has_system_role = any(
        assigned_role.role.name == "Superuser"
        for assigned_role in connection.user.roles
    )
    has_team_role = any(
        membership.team.slug == team_slug and membership.is_owner
        for membership in connection.user.teams
    )
    if connection.user.is_superuser or has_system_role or has_team_role:
        return

    msg = "Owner access is required to access this resource."
    raise PermissionDeniedException(detail=msg)


async def requires_operator_member(
    connection: ASGIConnection, _: BaseRouteHandler
) -> None:
    """Verify the connection user belongs to the operator team.

    The operator team is the first-party platform team (curates metrics,
    publishes security reports, onboards organizations). Use this guard for
    routes that any operator-team member is allowed to use.

    Args:
        connection: HTTP connection object.
        _: Route handler (unused).

    Raises:
        PermissionDeniedException: If the user is not a member of any team
            with ``is_operator=True``.
    """
    if _has_superuser_role(connection):
        return
    is_member, _editor, _admin = await _operator_membership_status(connection)
    if is_member:
        return
    raise PermissionDeniedException(
        detail="Operator team membership is required to access this resource."
    )


async def requires_operator_admin(
    connection: ASGIConnection, _: BaseRouteHandler
) -> None:
    """Verify the connection user is an admin or owner of the operator team.

    Use this guard for platform-administrative actions such as inviting new
    organizations or publishing security reports.

    Args:
        connection: HTTP connection object.
        _: Route handler (unused).

    Raises:
        PermissionDeniedException: If the user is not an admin or owner of
            the operator team.
    """
    if _has_superuser_role(connection):
        return
    _is_member, _editor, is_admin = await _operator_membership_status(connection)
    if is_admin:
        return
    raise PermissionDeniedException(
        detail="Operator team admin access is required to access this resource."
    )


async def requires_operator_tenant_admin(
    connection: ASGIConnection, _: BaseRouteHandler
) -> None:
    """Verify the user may perform cross-customer operator writes.

    Allows superusers and ``operator_tenant_admin`` (or operator-team
    owners). ``operator_support`` is read-only and refused here (PR-8,
    Control 2 — AC #29). Use this guard on every operator action that
    mutates a customer team: provisioning, SSO revert / recovery,
    force-unlock, ``enforced_provider`` changes.
    """
    if _has_superuser_role(connection):
        return
    _is_member, _editor, is_tenant_admin = await _operator_membership_status(connection)
    if is_tenant_admin:
        return
    raise PermissionDeniedException(
        detail="Operator tenant-admin access is required for this action."
    )


async def requires_operator_editor(
    connection: ASGIConnection, _: BaseRouteHandler
) -> None:
    """Verify the connection user is an editor, admin, or owner of the operator team.

    Use this guard for operator-team write actions that EDITOR-level
    members are allowed to perform (e.g. publishing manual metrics).
    Plain MEMBER role is rejected.

    Args:
        connection: HTTP connection object.
        _: Route handler (unused).

    Raises:
        PermissionDeniedException: If the user is not an editor/admin/owner
            of the operator team.
    """
    if _has_superuser_role(connection):
        return
    _is_member, is_editor, _admin = await _operator_membership_status(connection)
    if is_editor:
        return
    raise PermissionDeniedException(
        detail="Operator team editor access is required to access this resource."
    )
