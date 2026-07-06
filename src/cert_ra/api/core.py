# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

# pylint: disable=[invalid-name,import-outside-toplevel]
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from advanced_alchemy.extensions.litestar import SQLAlchemyPlugin
from advanced_alchemy.extensions.litestar.store import SQLAlchemyStore
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.config.csrf import CSRFConfig
from litestar.di import Provide
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.plugins import ScalarRenderPlugin, SwaggerRenderPlugin
from litestar.plugins import CLIPluginProtocol, InitPluginProtocol
from litestar.plugins.structlog import StructlogPlugin
from litestar.stores.registry import StoreRegistry
from litestar_email import EmailPlugin
from litestar_granian import GranianPlugin
from litestar_vite import VitePlugin

from cert_ra._version import __version__ as current_version
from cert_ra.db.models import SessionStore, User as UserModel
from cert_ra.db.storage import configure_storage
from cert_ra.settings.api import (
    AppSettings,
    get_app_settings,
    get_operator_team_settings,
    get_superuser_settings,
)

from . import config
from .domain.accounts.dependencies import provide_user
from .domain.accounts.guards import session_auth
from .domain.accounts.services import (
    EmailTokenService,
    RoleService,
    UserOAuthAccountService,
    UserRoleService,
    UserService,
)
from .domain.admin.services import AuditLogService
from .domain.listeners import get_listeners
from .domain.routes import get_route_handlers
from .domain.tags.services import TagService
from .domain.teams.services import (
    TeamInvitationService,
    TeamMemberService,
    TeamService,
)
from .lib import log
from .lib.email import get_email_config
from .lib.exceptions import get_exception_handlers
from .lib.vite import get_template_config, get_vite_config

if TYPE_CHECKING:
    from click import Group
    from litestar import Litestar
    from litestar.config.app import AppConfig


async def _ensure_superuser(_app: Litestar) -> None:
    """Create the bootstrap superuser on first startup if none exists.

    Reads ``CERT_RA_SUPERUSER_EMAIL`` / ``CERT_RA_SUPERUSER_PASSWORD`` from
    the environment. When ``password`` is not set the hook is a no-op, which
    makes superuser bootstrapping opt-in. If any superuser already exists the
    hook skips creation so it is safe to run on every restart.
    """
    import structlog

    logger = structlog.get_logger()
    settings = get_superuser_settings()
    if not settings.password:
        return

    async with config.alchemy.get_session() as db_session:
        service = UserService(session=db_session)
        if await service.get_one_or_none(is_superuser=True) is not None:
            return

        user = await service.create(
            {
                "email": settings.email,
                "password": settings.password,
                "is_superuser": True,
                "is_active": True,
                "is_verified": True,
                # Break-glass root: force a password rotation on first login.
                "must_change_password": True,
            },
            auto_commit=True,
        )
        await logger.ainfo(
            "superuser.created",
            email=user.email,
        )


async def _ensure_operator_team(_app: Litestar) -> None:
    """Provision the operator team on first startup if it does not yet exist."""
    settings = get_operator_team_settings()
    async with config.alchemy.get_session() as db_session:
        service = TeamService(session=db_session)
        await service.ensure_operator_team(
            name=settings.name,
            domain=settings.domain,
            enforced_provider=settings.enforced_provider,
        )


class ApplicationCore(InitPluginProtocol, CLIPluginProtocol):
    """Application core configuration plugin.

    This class is responsible for configuring the main Litestar application with our routes, guards, and various plugins

    """

    __slots__ = ("app_slug",)
    app_slug: str

    def __init__(self) -> None:
        """Initialize ``ApplicationConfigurator``."""

    def on_cli_init(self, cli: Group) -> None:  # noqa: ARG002
        """Configure CLI commands."""
        # from .cli import user_management_app # noqa: ERA001

        self.app_slug = "certora-risk-assessment"
        # cli.add_command(user_management_app) # noqa: ERA001

    def on_app_init(self, app_config: AppConfig) -> AppConfig:
        """Configure application for use with SQLAlchemy.

        Args:
            app_config: The :class:`AppConfig <.config.app.AppConfig>` instance.

        Returns:
            The modified :class:`AppConfig <.config.app.AppConfig>` instance.
        """
        app_settings = get_app_settings()

        self.app_slug = "certora-risk-assessment"
        app_config.debug = app_settings.debug
        # openapi
        app_config.openapi_config = OpenAPIConfig(
            title=app_settings.name,
            version=current_version,
            use_handler_docstrings=True,
            render_plugins=[
                ScalarRenderPlugin(version="latest"),
                SwaggerRenderPlugin(),
            ],
        )
        # session auth (updates openapi config)
        app_config = session_auth.on_app_init(app_config)
        # log
        from cert_ra.api.middleware.mfa_trap import MfaEnrollmentTrapMiddleware
        from cert_ra.api.middleware.no_team import NoTeamMiddleware
        from cert_ra.api.middleware.origin_check import OriginCheckMiddleware

        app_config.middleware.insert(0, log.StructlogMiddleware)
        # OriginCheck runs early — it shouldn't see anything past the
        # bare ASGI request. Before CSRF so we can fail fast on a
        # mismatched header without triggering the token check.
        app_config.middleware.insert(1, OriginCheckMiddleware)
        # MFA / NoTeam need the session populated by Litestar's auth
        # plugin, so they run after the route handlers attach the
        # session scope key. Append puts them at the end of the chain.
        app_config.middleware.append(MfaEnrollmentTrapMiddleware)
        app_config.middleware.append(NoTeamMiddleware)
        app_config.after_exception.append(log.after_exception_hook_handler)
        app_config.before_send.append(log.BeforeSendHandler())
        # Exception handlers for database errors (order matters - more specific first)
        app_config.exception_handlers.update(get_exception_handlers())
        # security
        app_config.cors_config = CORSConfig(
            allow_origins=app_settings.allowed_cors_origins
        )
        app_config.csrf_config = CSRFConfig(
            secret=app_settings.secret_key,
            cookie_secure=app_settings.csrf_cookie_secure,
            cookie_name=app_settings.csrf_cookie_name,
            header_name=app_settings.csrf_header_name,
        )
        # Compression
        app_config.compression_config = CompressionConfig(backend="brotli")
        # session store (advanced-alchemy, no redis)
        if app_config.stores is None:
            app_config.stores = {}
        session_store = SQLAlchemyStore(
            config.alchemy, model=SessionStore, namespace=app_settings.slug
        )
        if isinstance(app_config.stores, StoreRegistry):
            app_config.stores.register("sessions", session_store, allow_override=True)
        else:
            app_config.stores["sessions"] = session_store
        # templates
        app_config.template_config = get_template_config()
        # plugins

        app_config.plugins.extend(
            [
                StructlogPlugin(config=log.get_log_config()),
                VitePlugin(config=get_vite_config()),
                SQLAlchemyPlugin(config=config.alchemy),
                EmailPlugin(config=get_email_config()),
                GranianPlugin(),
            ]
        )

        # routes
        app_config.route_handlers.extend(get_route_handlers())
        # signatures
        app_config.signature_namespace.update(
            {
                "UUID": UUID,
                "UserModel": UserModel,
                "AuditLogService": AuditLogService,
                "EmailTokenService": EmailTokenService,
                "RoleService": RoleService,
                "TagService": TagService,
                "TeamInvitationService": TeamInvitationService,
                "TeamMemberService": TeamMemberService,
                "TeamService": TeamService,
                "UserOAuthAccountService": UserOAuthAccountService,
                "UserRoleService": UserRoleService,
                "UserService": UserService,
                "Settings": AppSettings,
            }
        )
        # dependencies
        app_config.dependencies.update(
            {
                "current_user": Provide(provide_user, sync_to_thread=False),
                "app_settings": Provide(get_app_settings, sync_to_thread=False),
            }
        )
        # listeners
        app_config.listeners.extend(get_listeners())
        # startup hooks — superuser must be created before the operator team
        app_config.on_startup.append(_ensure_superuser)
        app_config.on_startup.append(_ensure_operator_team)
        configure_storage()
        return app_config
