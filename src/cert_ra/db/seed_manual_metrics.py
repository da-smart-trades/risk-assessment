# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Replace manual ``ASSURANCE``/risk metrics for protocols from JSON.

Each payload is shaped::

    {
      "Name": "AAVE_V3",
      "metrics": [
        {
          "category": "TOKEN_RISK",
          "sub_category": "...",
          "name": "...",
          "desc": "...",
          "value": "...",
          "notes": "..."
        },
        ...
      ]
    }

For every payload the seeder:

1. Validates ``Name`` against :class:`cert_ra.types.ProtocolType`.
2. Deletes every ``manual_metric`` row whose ``protocol`` column equals
   ``Name``.
3. Inserts one row per element in ``metrics``, stamped with that
   ``protocol`` and authored by the first superuser found in the DB.

Steps 2 and 3 run inside a single transaction **per protocol**, so a
bad payload leaves both that protocol's existing rows and every
already-seeded protocol untouched.

The canonical payloads ship inside the wheel under
``db/fixtures/protocols/`` (next to ``role.json``), so the in-cluster
migrate image can seed without the repo on disk. Run with no arguments
to replace metrics for every packaged protocol — this is what
``upgrade.sh`` invokes via the ``certora-risk-seed-metrics`` console
entry point. Pass a file or directory to seed a specific payload
instead.

Usage:
    certora-risk-seed-metrics                       # all packaged payloads
    certora-risk-seed-metrics path/to/protocol.json # one payload
    certora-risk-seed-metrics path/to/dir/          # every *.json in dir
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cert_ra.db.engine_factory import create_sqlalchemy_engine
from cert_ra.db.models.manual_metric import ManualMetric
from cert_ra.db.models.user import User
from cert_ra.types import MetricCategory, ProtocolType
from cert_ra.utils import PACKAGE_ROOT

if TYPE_CHECKING:
    from uuid import UUID

USAGE_EXIT_CODE = 2
NO_SUPERUSER_EXIT_CODE = 1

# Packaged per-protocol payloads. Mirrors `db.utils.load_database_fixtures`'
# use of the in-package fixtures dir so the same path resolves both from a
# source checkout and from the installed wheel in the migrate container.
PROTOCOLS_DIR = PACKAGE_ROOT / "db" / "fixtures" / "protocols"


def _load_payload(path: Path) -> tuple[ProtocolType, list[dict[str, object]]]:
    """Read and validate the top-level JSON shape of one payload."""
    payload = json.loads(path.read_text())
    if "Name" not in payload:
        msg = f"{path}: missing required top-level 'Name' field"
        raise ValueError(msg)
    protocol = ProtocolType(str(payload["Name"]))
    metrics_in = payload.get("metrics") or []
    if not isinstance(metrics_in, list):
        msg = f"{path}: 'metrics' must be a JSON array"
        raise TypeError(msg)
    return protocol, metrics_in


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


def _build_rows(
    metrics_in: list[dict[str, object]],
    protocol: ProtocolType,
    author_id: UUID,
) -> list[ManualMetric]:
    """Turn one payload's ``metrics`` array into ``ManualMetric`` rows."""
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
                protocol=protocol,
                created_by=author_id,
                updated_by=author_id,
                # Seeded rows are the canonical, operator-published dataset
                # (shared scope: team_id stays NULL). They must be published so
                # the PROTOCOL_SCORE summary is favoritable — the favorites API
                # rejects draft (unpublished) metrics. Without this the protocol
                # star renders but every pin 400s with "still a draft".
                is_published=True,
            )
        )
    return rows


def _resolve_payloads(
    arg: str | None,
) -> list[tuple[ProtocolType, list[dict[str, object]]]]:
    """Resolve the CLI argument to a list of validated payloads.

    ``None`` → every packaged protocol payload. A directory → every
    ``*.json`` inside it. A file → that single payload.
    """
    target = Path(arg) if arg is not None else PROTOCOLS_DIR
    if target.is_dir():
        files = sorted(target.glob("*.json"))
        if not files:
            print(f"No *.json payloads found in {target}", file=sys.stderr)  # noqa: T201
            sys.exit(USAGE_EXIT_CODE)
        return [_load_payload(p) for p in files]
    if target.is_file():
        return [_load_payload(target)]
    print(f"Payload path not found: {target}", file=sys.stderr)  # noqa: T201
    sys.exit(USAGE_EXIT_CODE)


async def _seed(payloads: list[tuple[ProtocolType, list[dict[str, object]]]]) -> None:
    """Replace ``manual_metric`` rows for each payload, one txn per protocol."""
    engine = create_sqlalchemy_engine()
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    try:
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
                sys.exit(NO_SUPERUSER_EXIT_CODE)

            print(  # noqa: T201
                f"Authoring as superuser: {superuser.email} ({superuser.id})"
            )
            for protocol, metrics_in in payloads:
                print(  # noqa: T201
                    f"Replacing manual metrics for protocol {protocol.value}"
                )
                result = await session.execute(
                    delete(ManualMetric).where(ManualMetric.protocol == protocol)
                )
                deleted = getattr(result, "rowcount", 0)
                rows = _build_rows(metrics_in, protocol, superuser.id)
                session.add_all(rows)
                await session.commit()
                print(  # noqa: T201
                    f"Protocol {protocol.value}: deleted {deleted} existing rows, "
                    f"inserted {len(rows)} new rows."
                )
    finally:
        await engine.dispose()


def main() -> None:
    """Console entry point for ``certora-risk-seed-metrics``."""
    if len(sys.argv) > USAGE_EXIT_CODE:
        print(  # noqa: T201
            f"Usage: {sys.argv[0]} [path/to/file-or-dir]", file=sys.stderr
        )
        sys.exit(USAGE_EXIT_CODE)
    arg = sys.argv[1] if len(sys.argv) == USAGE_EXIT_CODE else None
    asyncio.run(_seed(_resolve_payloads(arg)))


if __name__ == "__main__":
    main()
