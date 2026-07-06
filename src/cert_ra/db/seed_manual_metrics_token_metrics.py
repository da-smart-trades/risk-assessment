# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

r"""Replace manual ANCHORS/CONTROL/ASSURANCE/TOKEN_SCORE metrics for tokens.

Seeds display metrics from JSON fixtures then computes and stores the
final probability-of-default as a TOKEN_SCORE row.

Anchor ``Risk score`` rows carry ``value`` = decimal PD (e.g. ``"0.02"``
for 2%).  Control and Assurance ``Multiplier`` rows carry ``value`` =
the numeric multiplier.  The seeder computes::

    PD_base     = 1 - prod(1 - pd_i)         anchor Risk score values
    M_control   = clamp(prod(m_k), 0.75, 1.25) CONTROL Multiplier values
    M_assurance = clamp(prod(m_j), 0.75, 1.25) ASSURANCE Multiplier values
    PD_final    = PD_base * M_control * M_assurance

and stores PD_final as a published TOKEN_SCORE / SUMMARY row (value is the
display-formatted percentage). This row is the token's favoritable PD card.
Weights default to 1; future user-configurable weighting will recompute
this row without touching the display rows.

For every payload the seeder:

1. Validates ``Name`` against :class:`cert_ra.types.TokenType`.
2. Deletes every ``manual_metric`` row whose ``token`` column equals
   ``Name`` (including any prior TOKEN_SCORE row).
3. Inserts display rows from the payload.
4. Computes PD_final and inserts the TOKEN_SCORE row.

Steps 2-4 run inside a single transaction per token.

Usage:
    certora-risk-seed-token-metrics                       # all packaged payloads
    certora-risk-seed-token-metrics path/to/token.json    # one payload
    certora-risk-seed-token-metrics path/to/dir/          # every *.json in dir
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cert_ra.db.engine_factory import create_sqlalchemy_engine
from cert_ra.db.models.manual_metric import ManualMetric
from cert_ra.db.models.user import User
from cert_ra.types import MetricCategory, TokenType
from cert_ra.utils import PACKAGE_ROOT

if TYPE_CHECKING:
    from uuid import UUID

USAGE_EXIT_CODE = 2
NO_SUPERUSER_EXIT_CODE = 1

TOKENS_DIR = PACKAGE_ROOT / "db" / "fixtures" / "tokens"

_CLAMP_LOW = 0.75
_CLAMP_HIGH = 1.25


def _load_payload(path: Path) -> tuple[TokenType, list[dict[str, object]]]:
    payload = json.loads(path.read_text())
    if "Name" not in payload:
        msg = f"{path}: missing required top-level 'Name' field"
        raise ValueError(msg)
    token = TokenType(str(payload["Name"]))
    metrics_in = payload.get("metrics") or []
    if not isinstance(metrics_in, list):
        msg = f"{path}: 'metrics' must be a JSON array"
        raise TypeError(msg)
    return token, metrics_in


def _opt(value: object) -> str | None:
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


def _build_display_rows(
    metrics_in: list[dict[str, object]],
    token: TokenType,
    author_id: UUID,
) -> list[ManualMetric]:
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
                token=token,
                created_by=author_id,
                updated_by=author_id,
                # Display rows are the canonical, operator-published dataset
                # (shared scope: team_id stays NULL). They must be published or
                # the visibility filter hides them from every non-superuser
                # viewer, leaving the token's metric listing empty. The computed
                # TOKEN_SCORE row below stays unpublished by design (read only by
                # the favorites resolver, never shown in listings).
                is_published=True,
            )
        )
    return rows


def _compute_pd_final(rows: list[ManualMetric]) -> float | None:
    """Return PD_final from display rows, or None if data is insufficient."""
    anchor_pds = [
        float(r.value)
        for r in rows
        if r.category == MetricCategory.ANCHORS
        and r.sub_category == "Risk score"
        and r.value
    ]
    if not anchor_pds:
        return None

    control_multipliers = [
        float(r.value)
        for r in rows
        if r.category == MetricCategory.CONTROL
        and r.sub_category == "Multiplier"
        and r.value
    ]
    assurance_multipliers = [
        float(r.value)
        for r in rows
        if r.category == MetricCategory.ASSURANCE
        and r.sub_category == "Multiplier"
        and r.value
    ]

    pd_base = 1.0 - math.prod(1.0 - pd for pd in anchor_pds)

    m_control = 1.0
    if control_multipliers:
        m_control = max(_CLAMP_LOW, min(_CLAMP_HIGH, math.prod(control_multipliers)))

    m_assurance = 1.0
    if assurance_multipliers:
        m_assurance = max(
            _CLAMP_LOW, min(_CLAMP_HIGH, math.prod(assurance_multipliers))
        )

    return pd_base * m_control * m_assurance


def _build_token_score_row(
    display_rows: list[ManualMetric],
    token: TokenType,
    author_id: UUID,
) -> ManualMetric | None:
    """Compute PD_final and return a TOKEN_SCORE row, or None if data is missing."""
    pd_final = _compute_pd_final(display_rows)
    if pd_final is None:
        return None
    return ManualMetric(
        name="Probability of default",
        desc=(
            "PD_final = PD_base * clamp(M_control, 0.75, 1.25) * clamp(M_assurance, 0.75, 1.25). "
            "PD_base = 1 - prod(1 - pd_i) over anchor Risk score rows. "
            "M_control = product of CONTROL Multiplier values. "
            "M_assurance = product of ASSURANCE Multiplier values. "
            "Weights default to 1; future user-configurable weighting will recompute this row."
        ),
        category=MetricCategory.TOKEN_SCORE,
        # Mirror the protocol PROTOCOL_SCORE/SUMMARY row exactly: sub_category
        # "SUMMARY" with a display-formatted percentage in ``value``. The token
        # page (ProtocolMetricsPanel) and the favorites resolver both read the
        # summary row's ``value`` directly, so this is the favoritable PD card.
        sub_category="SUMMARY",
        value=f"{pd_final * 100:.2f}%",
        notes=f"PD: {pd_final * 100:.4f}%",
        token=token,
        # Published so it is visible to every viewer and favoritable — the
        # favorites API rejects draft (unpublished) score rows.
        is_published=True,
        created_by=author_id,
        updated_by=author_id,
    )


def _resolve_payloads(
    arg: str | None,
) -> list[tuple[TokenType, list[dict[str, object]]]]:
    target = Path(arg) if arg is not None else TOKENS_DIR
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


async def _seed(payloads: list[tuple[TokenType, list[dict[str, object]]]]) -> None:
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
            for token, metrics_in in payloads:
                print(f"Replacing manual metrics for token {token.value}")  # noqa: T201
                result = await session.execute(
                    delete(ManualMetric).where(ManualMetric.token == token)
                )
                deleted = getattr(result, "rowcount", 0)

                display_rows = _build_display_rows(metrics_in, token, superuser.id)
                token_score_row = _build_token_score_row(
                    display_rows, token, superuser.id
                )

                all_rows = display_rows
                if token_score_row is not None:
                    all_rows = [*display_rows, token_score_row]
                    print(  # noqa: T201
                        f"  TOKEN_SCORE SUMMARY = {token_score_row.notes}"
                    )
                else:
                    print(  # noqa: T201
                        "  No anchor Risk score rows found — TOKEN_SCORE row skipped."
                    )

                session.add_all(all_rows)
                await session.commit()
                print(  # noqa: T201
                    f"Token {token.value}: deleted {deleted} existing rows, "
                    f"inserted {len(all_rows)} new rows "
                    f"({len(display_rows)} display + "
                    f"{1 if token_score_row else 0} TOKEN_SCORE)."
                )
    finally:
        await engine.dispose()


def main() -> None:
    """Console entry point for ``certora-risk-seed-token-metrics``."""
    if len(sys.argv) > USAGE_EXIT_CODE:
        print(  # noqa: T201
            f"Usage: {sys.argv[0]} [path/to/file-or-dir]", file=sys.stderr
        )
        sys.exit(USAGE_EXIT_CODE)
    arg = sys.argv[1] if len(sys.argv) == USAGE_EXIT_CODE else None
    asyncio.run(_seed(_resolve_payloads(arg)))


if __name__ == "__main__":
    main()
