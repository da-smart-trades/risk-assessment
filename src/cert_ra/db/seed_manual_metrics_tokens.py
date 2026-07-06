# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Seed manual ``TOKEN_RISK`` metrics for tokens from a packaged CSV.

The CSV columns are::

    name,desc,category,sub_category,chain,token,value,risk_score,notes

Only ``category=TOKEN_RISK`` rows with a non-empty ``token`` column are
accepted; ``chain`` / ``protocol`` / ``market`` must be empty, and
``sub_category`` / ``value`` / ``risk_score`` / ``notes`` may be blank
(treated as ``NULL``).

**Seed-once semantics.** ``TOKEN_RISK`` is operator-owned data (see
:class:`cert_ra.types.MetricCategory`) — operators curate it in the UI
after install, exactly like governance. The original generic seeder also
guarded on a non-empty table, so token risk was only ever written once.
This seeder preserves that: the default run no-ops if *any* ``TOKEN_RISK``
``manual_metric`` row already exists, so re-running install or an upgrade
never clobbers operator edits. Only a fresh install gets seeded.

Pass ``--force`` to bypass the guard and replace the TOKEN_RISK rows for
every token in the CSV (delete + re-insert per token, in one
transaction). This is a local-development affordance and is not used by
the deployment scripts.

The canonical CSV ships inside the wheel under ``db/fixtures/`` (next to
``role.json``), so the in-cluster migrate image can seed without the repo
on disk. ``infra/scripts/initial-setup.sh`` invokes this via the
``certora-risk-seed-tokens`` console entry point at first install.

Usage:
    certora-risk-seed-tokens                  # guarded; packaged CSV
    certora-risk-seed-tokens --force          # replace; packaged CSV
    certora-risk-seed-tokens path/to/file.csv # guarded; explicit CSV
"""

from __future__ import annotations

import asyncio
import csv
import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cert_ra.db.engine_factory import create_sqlalchemy_engine
from cert_ra.db.models.manual_metric import ManualMetric
from cert_ra.db.models.user import User
from cert_ra.types import MetricCategory, TokenType
from cert_ra.utils import PACKAGE_ROOT

USAGE_EXIT_CODE = 2
NO_SUPERUSER_EXIT_CODE = 1

# Packaged canonical CSV. Mirrors `db.utils.load_database_fixtures`' use of
# the in-package fixtures dir so the same path resolves both from a source
# checkout and from the installed wheel in the migrate container.
DEFAULT_CSV_PATH = PACKAGE_ROOT / "db" / "fixtures" / "seed_manual_metrics_tokens.csv"


def _opt(value: object) -> str | None:
    """Treat empty / whitespace-only strings as ``NULL``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require(row: dict[str, str], key: str) -> str:
    raw = row.get(key)
    if raw is None or raw.strip() == "":
        msg = f"row is missing required field {key!r}: {row!r}"
        raise ValueError(msg)
    return raw


def _load_rows(path: Path) -> dict[TokenType, list[dict[str, str]]]:
    """Read the CSV and group rows by token, validating each row."""
    grouped: dict[TokenType, list[dict[str, str]]] = defaultdict(list)
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            category = MetricCategory(_require(row, "category"))
            if category is not MetricCategory.TOKEN_RISK:
                msg = (
                    f"{path}: only category=TOKEN_RISK rows are allowed, "
                    f"got {category.value!r}: {row!r}"
                )
                raise ValueError(msg)
            token = TokenType(_require(row, "token"))
            for forbidden in ("chain", "protocol", "market"):
                if (row.get(forbidden) or "").strip():
                    msg = (
                        f"{path}: token-scoped TOKEN_RISK rows must leave "
                        f"{forbidden!r} empty: {row!r}"
                    )
                    raise ValueError(msg)
            grouped[token].append(row)
    return grouped


def _build_rows(
    rows_in: list[dict[str, str]],
    token: TokenType,
    author_id: object,
) -> list[ManualMetric]:
    """Turn one token's CSV rows into ``ManualMetric`` rows."""
    new_rows: list[ManualMetric] = []
    for row in rows_in:
        name = _require(row, "name")
        risk_score_raw = _opt(row.get("risk_score"))
        new_rows.append(
            ManualMetric(
                name=name,
                desc=_opt(row.get("desc")) or name,
                category=MetricCategory.TOKEN_RISK,
                sub_category=_opt(row.get("sub_category")),
                value=_opt(row.get("value")),
                risk_score=int(risk_score_raw) if risk_score_raw is not None else None,
                notes=_opt(row.get("notes")),
                token=token,
                created_by=author_id,
                updated_by=author_id,
            )
        )
    return new_rows


async def _seed(
    grouped: dict[TokenType, list[dict[str, str]]],
    *,
    force: bool,
) -> None:
    """Seed TOKEN_RISK rows once (or replace them per token when ``force``)."""
    engine = create_sqlalchemy_engine()
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    try:
        async with session_factory() as session:
            # Seed-once guard. Any existing TOKEN_RISK row means an operator
            # may have curated these in the UI — leave the table alone.
            if not force:
                existing = await session.scalar(
                    select(func.count())
                    .select_from(ManualMetric)
                    .where(ManualMetric.category == MetricCategory.TOKEN_RISK)
                )
                if existing:
                    print(  # noqa: T201
                        f"TOKEN_RISK metrics already present ({existing} rows); "
                        "leaving them untouched (use --force to replace)."
                    )
                    return

            superuser = await session.scalar(
                select(User).where(User.is_superuser.is_(True)).limit(1)
            )
            if superuser is None:
                print(  # noqa: T201
                    "No superuser found in the database. Create one first "
                    "(e.g. via the admin panel).",
                    file=sys.stderr,
                )
                sys.exit(NO_SUPERUSER_EXIT_CODE)

            print(  # noqa: T201
                f"Authoring as superuser: {superuser.email} ({superuser.id})"
            )

            total_deleted = 0
            total_inserted = 0
            for token, rows_in in grouped.items():
                # With force, replace the token's TOKEN_RISK subset. Without
                # force we only reach here on a fresh install, so the delete
                # is a harmless no-op that keeps the two paths identical.
                result = await session.execute(
                    delete(ManualMetric).where(
                        ManualMetric.token == token,
                        ManualMetric.category == MetricCategory.TOKEN_RISK,
                    )
                )
                deleted = getattr(result, "rowcount", 0) or 0
                new_rows = _build_rows(rows_in, token, superuser.id)
                session.add_all(new_rows)
                print(  # noqa: T201
                    f"Token {token.value}: deleted {deleted} existing rows, "
                    f"inserted {len(new_rows)} new rows."
                )
                total_deleted += deleted
                total_inserted += len(new_rows)

            await session.commit()
            print(  # noqa: T201
                f"Done. Deleted {total_deleted} rows, inserted {total_inserted} "
                f"rows across {len(grouped)} token(s)."
            )
    finally:
        await engine.dispose()


def main() -> None:
    """Console entry point for ``certora-risk-seed-tokens``."""
    args = sys.argv[1:]
    force = False
    if "--force" in args:
        force = True
        args = [a for a in args if a != "--force"]
    if len(args) > 1:
        print(  # noqa: T201
            f"Usage: {sys.argv[0]} [--force] [path/to/tokens.csv]",
            file=sys.stderr,
        )
        sys.exit(USAGE_EXIT_CODE)
    csv_path = Path(args[0]) if args else DEFAULT_CSV_PATH
    asyncio.run(_seed(_load_rows(csv_path), force=force))


if __name__ == "__main__":
    main()
