# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Minimal async Dune SQL client used by the throughput metric.

Submits an ad-hoc SQL query, polls until completion, and returns parsed rows.
Kept separate from the activities module so the HTTP machinery can be reused
if more Dune-backed metrics are added later.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping  # noqa: TC003 — runtime-resolved by pydantic

import httpx
from pydantic import BaseModel

from cert_ra.settings.dune import get_dune_settings

_TERMINAL_FAILURE_STATES = frozenset(
    {
        "QUERY_STATE_FAILED",
        "QUERY_STATE_CANCELLED",
        "QUERY_STATE_EXPIRED",
    }
)
_COMPLETED_STATE = "QUERY_STATE_COMPLETED"


class DuneExecution(BaseModel):
    execution_id: str
    state: str


class DuneExecutionStatus(BaseModel):
    execution_id: str
    state: str


class _DuneResults(BaseModel):
    rows: list[Mapping[str, object]]


class DuneResultsResponse(BaseModel):
    result: _DuneResults


class DuneError(RuntimeError):
    """Raised when a Dune query fails, is cancelled, or times out."""


async def run_dune_query(sql: str) -> list[Mapping[str, object]]:
    """Execute a Dune SQL query and return its result rows.

    Raises:
        DuneError: if the API key is not configured, the query enters a
            terminal failure state, or polling times out.
    """
    settings = get_dune_settings()
    if settings.api_key is None:
        msg = "Dune API key is not configured (set CERT_RA_DUNE_API_KEY)"
        raise DuneError(msg)

    headers = {"X-Dune-Api-Key": settings.api_key.get_secret_value()}

    async with httpx.AsyncClient(
        base_url=settings.base_url, headers=headers, timeout=60.0
    ) as client:
        submit = await client.post(
            "/sql/execute",
            json={"sql": sql, "performance": settings.performance},
        )
        submit.raise_for_status()
        execution = DuneExecution.model_validate_json(submit.content)

        deadline = asyncio.get_running_loop().time() + settings.poll_timeout_seconds
        state = execution.state

        while state != _COMPLETED_STATE:
            if state in _TERMINAL_FAILURE_STATES:
                msg = (
                    f"Dune query entered terminal state {state} "
                    f"(execution_id={execution.execution_id})"
                )
                raise DuneError(msg)
            if asyncio.get_running_loop().time() > deadline:
                msg = (
                    f"Dune query timed out after {settings.poll_timeout_seconds}s "
                    f"(execution_id={execution.execution_id})"
                )
                raise DuneError(msg)

            await asyncio.sleep(settings.poll_interval_seconds)
            status_resp = await client.get(
                f"/execution/{execution.execution_id}/status"
            )
            status_resp.raise_for_status()
            state = DuneExecutionStatus.model_validate_json(status_resp.content).state

        results_resp = await client.get(f"/execution/{execution.execution_id}/results")
        results_resp.raise_for_status()
        return DuneResultsResponse.model_validate_json(results_resp.content).result.rows
