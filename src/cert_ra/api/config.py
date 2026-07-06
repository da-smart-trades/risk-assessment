# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import Literal, cast

from advanced_alchemy.extensions.litestar import (
    AlembicAsyncConfig,
    AsyncSessionConfig,
    SQLAlchemyAsyncConfig,
)
from litestar.middleware.session.server_side import ServerSideSessionConfig

from cert_ra.db.engine_factory import create_sqlalchemy_engine
from cert_ra.settings.api import (
    get_app_settings,
)
from cert_ra.settings.db import get_db_settings


def create_session_config() -> ServerSideSessionConfig:
    """Create and return the server-side session configuration."""
    app = get_app_settings()
    samesite = app.session_cookie_samesite.lower()
    if samesite not in {"lax", "strict", "none"}:
        samesite = "lax"
    return ServerSideSessionConfig(
        key=app.session_cookie_name,
        max_age=app.session_max_age,
        renew_on_access=app.session_renew_on_access,
        secure=app.session_cookie_secure,
        samesite=cast("Literal['lax', 'strict', 'none']", samesite),
    )


def create_alchemy_config() -> SQLAlchemyAsyncConfig:
    """Create and return the SQLAlchemy async configuration."""
    db = get_db_settings()
    return SQLAlchemyAsyncConfig(
        engine_instance=create_sqlalchemy_engine(),
        before_send_handler="autocommit_include_redirects",
        session_config=AsyncSessionConfig(expire_on_commit=False),
        alembic_config=AlembicAsyncConfig(
            version_table_name=db.migration_ddl_version_table,
            script_config=str(db.migration_config),
            script_location=str(db.migration_path),
        ),
    )


session = create_session_config()
alchemy = create_alchemy_config()
