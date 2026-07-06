# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from temporalio import activity

from cert_ra.db.models import Release
from cert_ra.metrics._session import session_factory
from cert_ra.types import ChainType

from .schemas import ReleaseParams, ReleaseResult

_GITHUB_API = "https://api.github.com"


@activity.defn
async def fetch_last_release(params: ReleaseParams) -> ReleaseResult:
    """Fetch the most recent release timestamp from a GitHub repository.

    Raises:
        RuntimeError: if the GitHub response does not contain a usable
            ``published_at`` / ``created_at`` timestamp.
    """
    url = f"{_GITHUB_API}/repos/{params.repo}/releases/latest"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, dict):
        msg = f"releases: GitHub returned non-object payload for {params.repo}"
        raise TypeError(msg)

    released_at = payload.get("published_at") or payload.get("created_at")
    if not isinstance(released_at, str):
        msg = (
            f"releases: GitHub response for {params.repo} is missing "
            "published_at / created_at"
        )
        raise TypeError(msg)

    return ReleaseResult(
        chain=params.chain.upper(),
        repo=params.repo,
        released_at=released_at,
    )


@activity.defn
async def store_release(result: ReleaseResult) -> None:
    """Persist a release snapshot to the database."""
    raw = result.released_at
    # ``datetime.fromisoformat`` accepts ``Z`` directly only from 3.11+, but
    # not all versions normalise it onto UTC; coerce explicitly here.
    parsed = (
        datetime.fromisoformat(raw[:-1])
        if raw.endswith("Z")
        else datetime.fromisoformat(raw)
    )
    released_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    async with session_factory()() as session:
        session.add(
            Release(
                chain=ChainType(result.chain),
                repo=result.repo,
                released_at=released_at,
            )
        )
        await session.commit()
