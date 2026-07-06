# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Polygon operator-level data.

The Polygon Staking API already returns one row per named validator — each
row is its own operator (Polygon doesn't have a single entity running many
validators the way Ethereum does with Lido). We capture the same totals as
``fetch_polygon_stakes`` plus the operator name/owner from the API response,
then layer curated overrides on top for validators the API leaves unnamed.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from .labels import labels_for
from .rated import RatedOperator

_POLYGON_STAKING_URL = "https://staking-api.polygon.technology/api/v2/validators"
_PAGE_SIZE = 200


class _PolygonValidator(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    id: int
    name: str | None = None
    owner: str | None = None
    signer: str | None = None
    total_staked: float


class _PolygonSummary(BaseModel):
    total: int
    size: int


class _PolygonResponse(BaseModel):
    success: bool
    result: list[_PolygonValidator]
    summary: _PolygonSummary


def _resolve_name(v: _PolygonValidator, overrides: dict[str, str]) -> tuple[str, bool]:
    """Return ``(name, labeled)``. Labeled iff a curated override or the API
    gave us a non-empty name; falsey if we had to fall back to a slug.
    """
    for key in (v.owner, v.signer, str(v.id)):
        if key and key.lower() in overrides:
            return overrides[key.lower()], True
    if v.name and v.name.strip():
        return v.name.strip(), True
    if v.owner:
        return f"Validator {v.owner[:8]}…", False
    return f"Validator #{v.id}", False


async def fetch_polygon_operators() -> list[RatedOperator]:
    """Page through the Polygon Staking API and return one row per validator.

    ``total_staked`` is returned in wei and converted to POL (÷1e18).
    """
    overrides = {k.lower(): v for k, v in labels_for("POLYGON").items()}
    operators: list[RatedOperator] = []
    offset = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            resp = await client.get(
                _POLYGON_STAKING_URL,
                params={"offset": offset, "limit": _PAGE_SIZE},
            )
            resp.raise_for_status()
            parsed = _PolygonResponse.model_validate_json(resp.content)
            if not parsed.success:
                msg = "polygon operators: staking API reported success=false"
                raise RuntimeError(msg)
            if not parsed.result:
                break

            for v in parsed.result:
                name, labeled = _resolve_name(v, overrides)
                operators.append(
                    RatedOperator(
                        operator_id=str(v.id),
                        name=name,
                        validator_count=1,
                        total_effective_balance_eth=v.total_staked / 1e18,
                        labeled=labeled,
                    )
                )

            offset += parsed.summary.size
            if parsed.summary.total and offset >= parsed.summary.total:
                break

    return operators
