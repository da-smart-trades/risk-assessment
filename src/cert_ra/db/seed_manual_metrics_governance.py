# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Seed manual ``GOVERNANCE`` metrics for chains from a packaged CSV.

The CSV columns are::

    name,desc,category,sub_category,chain,token,value,risk_score,notes

Only ``category=GOVERNANCE`` rows with a non-empty ``chain`` column are
accepted; ``token`` / ``protocol`` / ``market`` must be empty, and
``sub_category`` / ``value`` / ``risk_score`` / ``notes`` may be blank
(treated as ``NULL``).

**Seed-once semantics.** Unlike the per-protocol seeder, governance
metrics are written to the database exactly once — at first install.
After that operators own them in the UI, so re-running must never clobber
their edits. The default run is therefore guarded: if *any*
``GOVERNANCE`` ``manual_metric`` row already exists, the seeder logs that
and exits 0 without touching the table. Only a genuinely empty (fresh)
install gets seeded.

Pass ``--force`` to bypass the guard and replace the GOVERNANCE rows for
every chain present in the CSV (delete + re-insert per chain, in one
transaction). This is a local-development affordance for iterating on the
CSV — it is not used by the deployment scripts.

The canonical CSV ships inside the wheel under ``db/fixtures/`` (next to
``role.json``), so the in-cluster migrate image can seed without the repo
on disk. ``infra/scripts/initial-setup.sh`` invokes this via the
``certora-risk-seed-governance`` console entry point at first install.

Usage:
    certora-risk-seed-governance                  # guarded; packaged CSV
    certora-risk-seed-governance --force          # replace; packaged CSV
    certora-risk-seed-governance path/to/file.csv # guarded; explicit CSV
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
from cert_ra.types import ChainType, MetricCategory
from cert_ra.utils import PACKAGE_ROOT

USAGE_EXIT_CODE = 2
NO_SUPERUSER_EXIT_CODE = 1

# Packaged canonical CSV. Mirrors `db.utils.load_database_fixtures`' use of
# the in-package fixtures dir so the same path resolves both from a source
# checkout and from the installed wheel in the migrate container.
DEFAULT_CSV_PATH = (
    PACKAGE_ROOT / "db" / "fixtures" / "seed_manual_metrics_governance.csv"
)


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


def _load_rows(path: Path) -> dict[ChainType, list[dict[str, str]]]:
    """Read the CSV and group rows by chain, validating each row."""
    grouped: dict[ChainType, list[dict[str, str]]] = defaultdict(list)
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            category = MetricCategory(_require(row, "category"))
            if category is not MetricCategory.GOVERNANCE:
                msg = (
                    f"{path}: only category=GOVERNANCE rows are allowed, "
                    f"got {category.value!r}: {row!r}"
                )
                raise ValueError(msg)
            chain = ChainType(_require(row, "chain"))
            for forbidden in ("token", "protocol", "market"):
                if (row.get(forbidden) or "").strip():
                    msg = (
                        f"{path}: chain-scoped GOVERNANCE rows must leave "
                        f"{forbidden!r} empty: {row!r}"
                    )
                    raise ValueError(msg)
            grouped[chain].append(row)
    return grouped


def _build_rows(
    rows_in: list[dict[str, str]],
    chain: ChainType,
    author_id: object,
) -> list[ManualMetric]:
    """Turn one chain's CSV rows into ``ManualMetric`` rows."""
    new_rows: list[ManualMetric] = []
    for row in rows_in:
        name = _require(row, "name")
        risk_score_raw = _opt(row.get("risk_score"))
        new_rows.append(
            ManualMetric(
                name=name,
                desc=_opt(row.get("desc")) or name,
                category=MetricCategory.GOVERNANCE,
                sub_category=_opt(row.get("sub_category")),
                value=_opt(row.get("value")),
                risk_score=int(risk_score_raw) if risk_score_raw is not None else None,
                notes=_opt(row.get("notes")),
                chain=chain,
                created_by=author_id,
                updated_by=author_id,
                # Seeded rows are the canonical, operator-published dataset
                # (shared scope: team_id stays NULL). They must be published or
                # they never surface — the chain dashboard, markets assurance,
                # and weighting profiles all filter on is_published=True, so an
                # unpublished governance row exists in the table but shows up
                # nowhere. Matches the protocol / token-metrics seeders.
                is_published=True,
            )
        )
    return new_rows


async def _seed(
    grouped: dict[ChainType, list[dict[str, str]]],
    *,
    force: bool,
) -> None:
    """Seed GOVERNANCE rows once (or replace them per chain when ``force``)."""
    engine = create_sqlalchemy_engine()
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    try:
        async with session_factory() as session:
            # Seed-once guard. Any existing GOVERNANCE row means an operator
            # may have curated these in the UI — leave the table alone.
            if not force:
                existing = await session.scalar(
                    select(func.count())
                    .select_from(ManualMetric)
                    .where(ManualMetric.category == MetricCategory.GOVERNANCE)
                )
                if existing:
                    print(  # noqa: T201
                        f"GOVERNANCE metrics already present ({existing} rows); "
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
            for chain, rows_in in grouped.items():
                # With force, replace the chain's GOVERNANCE subset. Without
                # force we only reach here on a fresh install, so the delete
                # is a harmless no-op that keeps the two paths identical.
                result = await session.execute(
                    delete(ManualMetric).where(
                        ManualMetric.chain == chain,
                        ManualMetric.category == MetricCategory.GOVERNANCE,
                    )
                )
                deleted = getattr(result, "rowcount", 0) or 0
                new_rows = _build_rows(rows_in, chain, superuser.id)
                session.add_all(new_rows)
                print(  # noqa: T201
                    f"Chain {chain.value}: deleted {deleted} existing rows, "
                    f"inserted {len(new_rows)} new rows."
                )
                total_deleted += deleted
                total_inserted += len(new_rows)

            await session.commit()
            print(  # noqa: T201
                f"Done. Deleted {total_deleted} rows, inserted {total_inserted} "
                f"rows across {len(grouped)} chain(s)."
            )
    finally:
        await engine.dispose()


def main() -> None:
    """Console entry point for ``certora-risk-seed-governance``."""
    args = sys.argv[1:]
    force = False
    if "--force" in args:
        force = True
        args = [a for a in args if a != "--force"]
    if len(args) > 1:
        print(  # noqa: T201
            f"Usage: {sys.argv[0]} [--force] [path/to/governance.csv]",
            file=sys.stderr,
        )
        sys.exit(USAGE_EXIT_CODE)
    csv_path = Path(args[0]) if args else DEFAULT_CSV_PATH
    asyncio.run(_seed(_load_rows(csv_path), force=force))


if __name__ == "__main__":
    main()
