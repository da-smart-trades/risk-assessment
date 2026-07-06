# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from functools import cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cert_ra.db.engine_factory import create_sqlalchemy_engine


@cache
def session_factory() -> async_sessionmaker[AsyncSession]:
    """Return a cached async sessionmaker bound to the shared engine.

    Used by Temporal activities to persist metric snapshots.
    """
    return async_sessionmaker(create_sqlalchemy_engine(), expire_on_commit=False)
