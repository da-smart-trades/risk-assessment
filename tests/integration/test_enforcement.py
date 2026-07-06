# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Integration tests for per-team ``enforced_provider`` (PR-7).

Covers the enforcement set/unset authorization + precondition matrix and
the stuck-list view. The OIDC self-migration happy path needs a live IdP
handshake and is exercised at the helper level in
``tests/unit/lib/test_team_policy.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import func, select

from cert_ra.db.models import AuditAction, AuditLog, Team
from cert_ra.settings.api import get_feature_settings

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

# user@example.com owns this team (see tests/data_fixtures.py).
TEST_TEAM_ID = "97108ac1-ffcb-411d-8b1e-d9183399f63b"
OWNER_ID = "5ef29f3c-3560-4d15-ba6b-a2e5c721e4d2"
OWNER_EMAIL = "user@example.com"
OWNER_PASSWORD = "Test_Password2!"  # noqa: S105
NON_OWNER_EMAIL = "another@example.com"
NON_OWNER_PASSWORD = "Test_Password3!"  # noqa: S105


@pytest.fixture(name="_enforcement_on")
def fx_enforcement_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flip ``cert_ra_features_enforced_provider`` on for the test."""
    monkeypatch.setattr(get_feature_settings(), "enforced_provider", True)


async def _login(client: AsyncClient, email: str, password: str) -> dict[str, str]:
    """Log in and return headers (XSRF + cookie) for subsequent calls."""
    client.cookies.clear()
    await client.get("/login")
    csrf = client.cookies.get("XSRF-TOKEN") or ""
    resp = await client.post(
        "/login/",
        json={"username": email, "password": password},
        headers={"X-XSRF-TOKEN": csrf, "Content-Type": "application/json"},
        follow_redirects=False,
    )
    csrf = resp.cookies.get("XSRF-TOKEN") or csrf
    cookies = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    return {
        "X-XSRF-TOKEN": csrf,
        "Content-Type": "application/json",
        "Cookie": cookies,
    }


async def test_get_404_when_flag_off(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """AC #113: with the flag off the page returns 404."""
    resp = await client.get(
        f"/teams/{TEST_TEAM_ID}/enforcement/",
        headers=user_token_headers,
        follow_redirects=False,
    )
    assert resp.status_code == 404


async def test_set_404_when_flag_off(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """AC #113: with the flag off the set endpoint returns 404."""
    resp = await client.post(
        f"/teams/{TEST_TEAM_ID}/enforcement/",
        json={"provider": "google"},
        headers=user_token_headers,
        follow_redirects=False,
    )
    assert resp.status_code == 404


@pytest.mark.usefixtures("_enforcement_on")
async def test_set_requires_owner(client: AsyncClient) -> None:
    """AC #40: a non-owner setting enforced_provider gets 403."""
    headers = await _login(client, NON_OWNER_EMAIL, NON_OWNER_PASSWORD)
    resp = await client.post(
        f"/teams/{TEST_TEAM_ID}/enforcement/",
        json={"provider": "google"},
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code == 403


@pytest.mark.usefixtures("_enforcement_on")
async def test_set_409_when_acting_user_wrong_provider(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """AC #41: owner not signed in via the target provider gets 409."""
    resp = await client.post(
        f"/teams/{TEST_TEAM_ID}/enforcement/",
        json={"provider": "microsoft"},
        headers=user_token_headers,
        follow_redirects=False,
    )
    assert resp.status_code == 409


@pytest.mark.usefixtures("_enforcement_on")
async def test_unset_succeeds_for_owner(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """AC #43: unsetting succeeds unconditionally (no provider precondition)."""
    resp = await client.post(
        f"/teams/{TEST_TEAM_ID}/enforcement/",
        json={"provider": ""},
        headers=user_token_headers,
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302, 303)


async def test_switch_provider_404_when_flag_off(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """The settings-initiated switch endpoint is dark when the flag is off."""
    resp = await client.post(
        "/profile/switch-provider/google",
        headers=user_token_headers,
        follow_redirects=False,
    )
    assert resp.status_code == 404


@pytest.mark.usefixtures("_enforcement_on")
async def test_switch_provider_noop_without_enforcement(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """With no enforcing team, the switch is a no-op redirect (no cookie)."""
    resp = await client.post(
        "/profile/switch-provider/google",
        headers=user_token_headers,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "pending_provider_switch_token" not in resp.cookies


@pytest.mark.usefixtures("_enforcement_on")
async def test_reminder_is_throttled_to_one_per_window(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    session: AsyncSession,
) -> None:
    """A second reminder within 48h is suppressed (one audit row only)."""
    team = await session.get(Team, TEST_TEAM_ID)
    assert team is not None
    team.enforced_provider = "google"
    team.enforced_provider_set_at = datetime.now(UTC)
    await session.commit()

    body = {"member_id": OWNER_ID}
    first = await client.post(
        f"/teams/{TEST_TEAM_ID}/enforcement/remind/",
        json=body,
        headers=user_token_headers,
        follow_redirects=False,
    )
    assert first.status_code in (302, 303)
    second = await client.post(
        f"/teams/{TEST_TEAM_ID}/enforcement/remind/",
        json=body,
        headers=user_token_headers,
        follow_redirects=False,
    )
    assert second.status_code in (302, 303)

    count = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.TEAM_ENFORCEMENT_REMINDER.value,
            AuditLog.target_id == UUID(OWNER_ID),
        )
    )
    assert count == 1


@pytest.mark.usefixtures("_enforcement_on")
async def test_stuck_list_requires_membership(client: AsyncClient) -> None:
    """AC #110/#111: a non-member cannot read the stuck list."""
    headers = await _login(client, NON_OWNER_EMAIL, NON_OWNER_PASSWORD)
    resp = await client.get(
        f"/teams/{TEST_TEAM_ID}/enforcement/stuck/",
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code == 403


@pytest.mark.usefixtures("_enforcement_on")
async def test_stuck_list_visible_to_owner(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """AC #110: an owner can read the (empty) stuck list."""
    resp = await client.get(
        f"/teams/{TEST_TEAM_ID}/enforcement/stuck/",
        headers=user_token_headers,
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert resp.json()["stuckMembers"] == []
