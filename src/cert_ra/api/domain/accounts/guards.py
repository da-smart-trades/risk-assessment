# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from litestar.exceptions import PermissionDeniedException
from litestar.middleware.session.server_side import ServerSideSessionBackend
from litestar.security.session_auth import SessionAuth
from litestar_vite.inertia import share

from cert_ra.api.config import (
    alchemy,
    session as session_config,
)
from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.schemas import User as UserSchema
from cert_ra.api.domain.teams.schemas import CurrentTeam
from cert_ra.api.lib.team_context import select_default_team
from cert_ra.db.models import User as UserModel

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from litestar.connection import ASGIConnection
    from litestar.handlers.base import BaseRouteHandler

    from cert_ra.api.domain.accounts.services import UserService


__all__ = (
    "current_user_from_session",
    "requires_active_user",
    "requires_superuser",
    "requires_verified_user",
    "session_auth",
)


def requires_active_user(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Request requires active user.

    Verifies the request user is active.

    Args:
        connection (ASGIConnection): HTTP Request
        _ (BaseRouteHandler): Route handler

    Raises:
        PermissionDeniedException: Permission denied exception
    """
    if connection.user.is_active:
        return
    msg = "Your user account is inactive."
    raise PermissionDeniedException(detail=msg)


def requires_superuser(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Request requires active superuser.

    Args:
        connection (ASGIConnection): HTTP Request
        _ (BaseRouteHandler): Route handler

    Raises:
        PermissionDeniedException: Permission denied exception
    """
    if connection.user.is_superuser:
        return
    msg = "Your account does not have enough privileges to access this content."
    raise PermissionDeniedException(detail=msg)


def requires_verified_user(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Verify the connection user is a superuser.

    Args:
        connection (ASGIConnection): Request/Connection object.
        _ (BaseRouteHandler): Route handler.

    Raises:
        PermissionDeniedException: Not authorized
    """
    if connection.user.is_verified:
        return
    msg = "Your account has not been verified."
    raise PermissionDeniedException(detail=msg)


async def current_user_from_session(
    session: dict[str, Any],
    connection: ASGIConnection[Any, Any, Any, Any],
) -> UserModel | None:
    """Lookup current user from server session state.

    Fetches the user information from the database


    Args:
        session (dict[str,Any]): Litestar session dictionary
        connection (ASGIConnection[Any, Any, Any, Any]): ASGI connection.

    Returns:
        User: User record mapped to the JWT identifier
    """
    if (user_id := session.get("user_id")) is None:
        share(connection, "auth", {"isAuthenticated": False})
        return None
    service_provider: AsyncGenerator[UserService, None] = provide_users_service(
        alchemy.provide_session(connection.app.state, connection.scope),
    )
    try:
        service = await anext(service_provider)
        user = await service.get_one_or_none(email=user_id)
        if user and user.is_active:
            # enforced_provider_for_user short-circuits to None (no
            # query) while the feature flag is off, so this stays free
            # in the common case and surfaces the "switch provider"
            # affordance only when a team enforces one.
            from cert_ra.api.lib.team_policy import enforced_provider_for_user

            enforced_provider = await enforced_provider_for_user(
                service.repository.session, user
            )
            share(
                connection,
                "auth",
                {
                    "isAuthenticated": True,
                    "user": service.to_schema(user, schema_type=UserSchema),
                    "enforcedProvider": enforced_provider,
                },
            )
            # Default the active team from membership so a team-scoped
            # profile/dashboard applies without first using the switcher.
            # Only when nothing is selected; an explicit switch wins.
            if not session.get("currentTeam"):
                default_member = select_default_team(user)
                if default_member is not None:
                    session["currentTeam"] = CurrentTeam(
                        team_id=default_member.team_id,
                        team_name=default_member.team.name,
                    )
            return user
    finally:
        await service_provider.aclose()
    session.pop("user_id", None)
    share(connection, "auth", {"isAuthenticated": False})
    return None


session_auth = SessionAuth[UserModel, ServerSideSessionBackend](
    session_backend_config=session_config,
    retrieve_user_handler=current_user_from_session,
    exclude=[
        "^/schema",
        "^/health",
        "^/login",
        "^/forgot-password",
        "^/reset-password",
        "^/verify-email",
        "^/mfa-challenge",
        "^/auth/",
    ],
)
