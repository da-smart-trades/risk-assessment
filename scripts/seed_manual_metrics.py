#!/usr/bin/env python3
"""Seed manual metrics from CSV into the database.

Reads a CSV (default: scripts/seed_manual_metrics.csv) and inserts all rows,
stamping created_by / updated_by with the first superuser found in the
database.

Usage:
    uv run python scripts/seed_manual_metrics.py [path/to/file.csv]

The script is idempotent: if the manual_metric table already contains rows
it exits without inserting duplicates.
"""

from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cert_ra.db.models.manual_metric import ManualMetric
from cert_ra.db.models.user import User
from cert_ra.settings.db import get_db_settings
from cert_ra.types import ChainType, MetricCategory, TokenType

DEFAULT_CSV_PATH = Path(__file__).with_name("seed_manual_metrics.csv")


async def main(csv_path: Path) -> None:
    """Insert manual metrics from CSV using the first superuser as author."""
    settings = get_db_settings()
    engine = create_async_engine(settings.url, echo=False)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    async with session_factory() as session:
        existing_count = await session.scalar(
            select(func.count()).select_from(ManualMetric)
        )
        if existing_count:
            print(  # noqa: T201
                f"manual_metric table already has {existing_count} rows — skipping seed."
            )
            await engine.dispose()
            return

        superuser = await session.scalar(
            select(User).where(User.is_superuser.is_(True)).limit(1)
        )
        if superuser is None:
            print(  # noqa: T201
                "No superuser found in the database. Create one first (e.g. via the admin panel)."
            )
            await engine.dispose()
            return

        print(f"Seeding as superuser: {superuser.email} ({superuser.id})")  # noqa: T201
        print(f"Reading rows from {csv_path}")  # noqa: T201

        with csv_path.open(newline="") as f:
            rows: list[ManualMetric] = [
                ManualMetric(
                    name=row["name"],
                    desc=row["desc"],
                    category=MetricCategory(row["category"]),
                    sub_category=row["sub_category"] or None,
                    chain=ChainType(row["chain"]) if row["chain"] else None,
                    token=TokenType(row["token"]) if row["token"] else None,
                    value=row["value"] or None,
                    risk_score=int(row["risk_score"]) if row["risk_score"] else None,
                    notes=row["notes"] or None,
                    created_by=superuser.id,
                    updated_by=superuser.id,
                )
                for row in csv.DictReader(f)
            ]

        session.add_all(rows)
        await session.commit()
        print(f"Seeded {len(rows)} manual metrics.")  # noqa: T201

    await engine.dispose()


csv_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV_PATH
asyncio.run(main(csv_arg))
