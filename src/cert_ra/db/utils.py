# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations


async def load_database_fixtures() -> None:
    """Import/Synchronize Database Fixtures."""
    from advanced_alchemy.utils.fixtures import open_fixture_async
    from sqlalchemy import select
    from sqlalchemy.orm import load_only
    from structlog import get_logger

    from cert_ra.api.config import alchemy
    from cert_ra.api.domain.accounts.services import RoleService
    from cert_ra.db.models import Role
    from cert_ra.settings.db import get_db_settings

    fixtures_path = get_db_settings().fixture_path
    logger = get_logger()
    async with RoleService.new(
        statement=select(Role).options(
            load_only(Role.id, Role.slug, Role.name, Role.description)
        ),
        config=alchemy,
    ) as service:
        fixture_data = await open_fixture_async(fixtures_path, "role")
        await service.upsert_many(
            match_fields=["name"], data=fixture_data, auto_commit=True
        )
        await logger.ainfo("loaded roles")
