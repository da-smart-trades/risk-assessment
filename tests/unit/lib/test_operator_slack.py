# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unit tests for the operator-action Slack fan-out (PR-8, Control 3)."""
# ruff: noqa: ANN401, ARG002, PYI034, EM101 — httpx test-double boilerplate

from __future__ import annotations

from typing import Any

import httpx
import pytest

from cert_ra.api.domain.accounts.signals import _post_operator_action_to_slack
from cert_ra.settings.api import get_operator_team_settings

pytestmark = pytest.mark.anyio

_CALLS: list[tuple[str, dict[str, Any]]] = []


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any] | None = None) -> _FakeResponse:
        _CALLS.append((url, json or {}))
        return _FakeResponse()


@pytest.fixture(autouse=True)
def _reset_calls() -> None:
    _CALLS.clear()


async def test_no_post_when_webhook_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_operator_team_settings(), "slack_webhook_url", "")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    await _post_operator_action_to_slack(
        action="reset_mfa_only", actor_email="op@certora.com", team_name="Acme"
    )
    assert _CALLS == []


async def test_posts_to_slack_when_webhook_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        get_operator_team_settings(),
        "slack_webhook_url",
        "https://hooks.slack.test/abc",
    )
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    await _post_operator_action_to_slack(
        action="total_recovery", actor_email="op@certora.com", team_name="Acme"
    )
    assert len(_CALLS) == 1
    url, payload = _CALLS[0]
    assert url == "https://hooks.slack.test/abc"
    assert "total_recovery" in payload["text"]
    assert "op@certora.com" in payload["text"]
    assert "Acme" in payload["text"]


async def test_slack_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A webhook error must not propagate (fan-out is best-effort)."""

    class _BoomClient(_FakeClient):
        async def post(self, url: str, json: dict[str, Any] | None = None) -> Any:
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(
        get_operator_team_settings(),
        "slack_webhook_url",
        "https://hooks.slack.test/abc",
    )
    monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)
    # Does not raise.
    await _post_operator_action_to_slack(
        action="force_unlock", actor_email="op@certora.com", team_name=None
    )
