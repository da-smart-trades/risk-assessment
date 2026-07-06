#!/usr/bin/env python3
"""Replace manual metrics for one market from a JSON payload.

The JSON file is shaped::

    {
      "Name": "AAVE_USDC",
      "metrics": [
        {
          "category": "ANCHORS",
          "sub_category": "...",
          "name": "...",
          "desc": "...",
          "value": "...",
          "notes": "..."
        },
        ...
      ]
    }

The script:

1. Validates ``Name`` against :class:`cert_ra.types.MarketType`.
2. Deletes every ``manual_metric`` row whose ``market`` column equals
   ``Name``.
3. Inserts one row per element in ``metrics``, stamped with that
   ``market`` and authored by the first superuser found in the DB.

Steps 2 and 3 run inside a single transaction, so a partial failure
leaves the table untouched.

Usage:
    uv run python scripts/seed_manual_metrics_market.py path/to/file.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cert_ra.db.models.manual_metric import ManualMetric
from cert_ra.db.models.user import User
from cert_ra.settings.db import get_db_settings
from cert_ra.types import MarketType, MetricCategory

EXPECTED_ARGC = 2
USAGE_EXIT_CODE = 2


def _load_payload(path: Path) -> tuple[MarketType, list[dict[str, object]]]:
    """Read and validate the top-level JSON shape."""
    payload = json.loads(path.read_text())
    if "Name" not in payload:
        msg = f"{path}: missing required top-level 'Name' field"
        raise ValueError(msg)
    market = MarketType(str(payload["Name"]))
    metrics_in = payload.get("metrics") or []
    if not isinstance(metrics_in, list):
        msg = f"{path}: 'metrics' must be a JSON array"
        raise TypeError(msg)
    return market, metrics_in


def _opt(value: object) -> str | None:
    """Treat empty / whitespace-only strings as ``NULL``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require(metric: dict[str, object], key: str) -> str:
    raw = metric.get(key)
    if raw is None or str(raw).strip() == "":
        msg = f"metric is missing required field {key!r}: {metric!r}"
        raise ValueError(msg)
    return str(raw)


async def main(market: MarketType, metrics_in: list[dict[str, object]]) -> None:
    """Replace manual_metric rows for one market from a JSON payload."""
    settings = get_db_settings()
    engine = create_async_engine(settings.url, echo=False)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    async with session_factory() as session:
        superuser = await session.scalar(
            select(User).where(User.is_superuser.is_(True)).limit(1)
        )
        if superuser is None:
            print(  # noqa: T201
                "No superuser found in the database. Create one first "
                "(e.g. via the admin panel).",
                file=sys.stderr,
            )
            await engine.dispose()
            sys.exit(1)

        print(  # noqa: T201
            f"Authoring as superuser: {superuser.email} ({superuser.id})"
        )
        print(f"Replacing manual metrics for market {market.value}")  # noqa: T201

        result = await session.execute(
            delete(ManualMetric).where(ManualMetric.market == market)
        )
        deleted = getattr(result, "rowcount", 0)

        rows: list[ManualMetric] = []
        for metric in metrics_in:
            name = _require(metric, "name")
            rows.append(
                ManualMetric(
                    name=name,
                    desc=_opt(metric.get("desc")) or name,
                    category=MetricCategory(_require(metric, "category")),
                    sub_category=_opt(metric.get("sub_category")),
                    value=_opt(metric.get("value")),
                    notes=_opt(metric.get("notes")),
                    market=market,
                    created_by=superuser.id,
                    updated_by=superuser.id,
                )
            )
        session.add_all(rows)
        await session.commit()

        print(  # noqa: T201
            f"Market {market.value}: deleted {deleted} existing rows, "
            f"inserted {len(rows)} new rows."
        )

    await engine.dispose()


if len(sys.argv) != EXPECTED_ARGC:
    print(  # noqa: T201
        f"Usage: {sys.argv[0]} path/to/market.json", file=sys.stderr
    )
    sys.exit(USAGE_EXIT_CODE)

_market, _metrics_in = _load_payload(Path(sys.argv[1]))
asyncio.run(main(_market, _metrics_in))
