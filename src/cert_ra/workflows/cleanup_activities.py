# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Activities backing ``StateRowCleanupWorkflow``.

A single parameterized activity that DELETEs rows matching a retention
clause from a named table. The workflow iterates the registered list
and invokes this activity once per table.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from temporalio import activity

from cert_ra.db.engine_factory import create_sqlalchemy_engine

# Hard allowlist of table identifiers. The workflow only ever invokes
# this activity with values from ``CLEANUP_TARGETS``, but defense in
# depth — refuse anything that doesn't look like a snake_case identifier
# so a future bug can't introduce SQL injection via the table param.
_VALID_TABLE_NAME = re.compile(r"\A[a-z_][a-z0-9_]*\Z")


@activity.defn
async def delete_expired_rows(table: str, retention_clause: str) -> int:
    """Delete rows from ``table`` matching ``retention_clause``.

    Args:
        table: Snake-case table name (e.g., ``"pending_oidc_link"``).
            Validated against a strict regex to prevent injection.
        retention_clause: WHERE-clause fragment. The caller is
            responsible for keeping this side-effect-free; it's not
            parameterized because Postgres planner can't bind WHERE
            shapes across calls.

    Returns:
        Number of rows deleted.

    Raises:
        ValueError: If ``table`` is not a valid identifier.
    """
    if not _VALID_TABLE_NAME.match(table):
        msg = f"invalid table identifier (refused): {table!r}"
        raise ValueError(msg)
    sql = text(f"DELETE FROM {table} WHERE {retention_clause}")  # noqa: S608
    engine = create_sqlalchemy_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(sql)
        await session.commit()
        # Result.rowcount is valid for DML but missing from some SQLAlchemy
        # type stubs; access via getattr to keep pyright happy.
        rowcount = getattr(result, "rowcount", 0) or 0
    await engine.dispose()
    return int(rowcount)
