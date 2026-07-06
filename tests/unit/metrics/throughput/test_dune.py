# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for the Dune SQL client used by the throughput metric."""

# ruff: noqa: EM102, TRY003 — f-string AssertionError is fine in mock handlers.

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import SecretStr

from cert_ra.metrics.throughput import dune
from cert_ra.settings.dune import DuneSettings

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


def _patch_settings(
    mocker: MockerFixture,
    *,
    api_key: str | None = "key",
    poll_interval: float = 0.0,
    poll_timeout: float = 5.0,
) -> None:
    settings = DuneSettings(
        api_key=SecretStr(api_key) if api_key is not None else None,
        poll_interval_seconds=poll_interval,
        poll_timeout_seconds=poll_timeout,
    )
    mocker.patch.object(dune, "get_dune_settings", return_value=settings)


def _install_transport(mocker: MockerFixture, handler: httpx.MockTransport) -> None:
    original = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = handler
        return original(*args, **kwargs)  # type: ignore[arg-type]

    mocker.patch.object(dune.httpx, "AsyncClient", side_effect=factory)


async def test_run_dune_query_without_api_key_raises(mocker: MockerFixture) -> None:
    _patch_settings(mocker, api_key=None)
    with pytest.raises(dune.DuneError, match="API key is not configured"):
        await dune.run_dune_query("SELECT 1")


async def test_run_dune_query_returns_rows_on_immediate_completion(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sql/execute"):
            return httpx.Response(
                200,
                json={"execution_id": "exec-1", "state": "QUERY_STATE_COMPLETED"},
            )
        if request.url.path.endswith("/results"):
            return httpx.Response(200, json={"result": {"rows": [{"value": 42}]}})
        # Status poll — shouldn't be reached because state is already COMPLETED.
        raise AssertionError(f"Unexpected URL {request.url}")

    _install_transport(mocker, httpx.MockTransport(handler))

    rows = await dune.run_dune_query("SELECT 1")
    assert rows == [{"value": 42}]


async def test_run_dune_query_polls_until_completion(mocker: MockerFixture) -> None:
    _patch_settings(mocker)

    states = iter(["QUERY_STATE_EXECUTING", "QUERY_STATE_COMPLETED"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sql/execute"):
            return httpx.Response(
                200,
                json={"execution_id": "exec-2", "state": "QUERY_STATE_PENDING"},
            )
        if request.url.path.endswith("/status"):
            return httpx.Response(
                200, json={"execution_id": "exec-2", "state": next(states)}
            )
        if request.url.path.endswith("/results"):
            return httpx.Response(200, json={"result": {"rows": [{"v": 1}]}})
        raise AssertionError(f"Unexpected URL {request.url}")

    _install_transport(mocker, httpx.MockTransport(handler))

    rows = await dune.run_dune_query("SELECT 1")
    assert rows == [{"v": 1}]


async def test_run_dune_query_raises_on_terminal_failure(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sql/execute"):
            return httpx.Response(
                200, json={"execution_id": "exec-3", "state": "QUERY_STATE_FAILED"}
            )
        raise AssertionError(f"Unexpected URL {request.url}")

    _install_transport(mocker, httpx.MockTransport(handler))

    with pytest.raises(dune.DuneError, match="QUERY_STATE_FAILED"):
        await dune.run_dune_query("SELECT 1")


async def test_run_dune_query_times_out(mocker: MockerFixture) -> None:
    # Poll loop never sees a completed state; timeout must fire.
    _patch_settings(mocker, poll_interval=0.0, poll_timeout=0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sql/execute"):
            return httpx.Response(
                200,
                json={"execution_id": "exec-4", "state": "QUERY_STATE_EXECUTING"},
            )
        if request.url.path.endswith("/status"):
            return httpx.Response(
                200,
                json={"execution_id": "exec-4", "state": "QUERY_STATE_EXECUTING"},
            )
        raise AssertionError(f"Unexpected URL {request.url}")

    _install_transport(mocker, httpx.MockTransport(handler))

    # Drive past the 0-second deadline.
    await asyncio.sleep(0)

    with pytest.raises(dune.DuneError, match="timed out"):
        await dune.run_dune_query("SELECT 1")
