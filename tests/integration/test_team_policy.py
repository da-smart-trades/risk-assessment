# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unit tests for per-team IDP enforcement helpers (PR-7).

Exercises ``assert_team_provider_allowed`` (flag gating + the enforcement
raise) and ``find_stuck_members`` against the real DB via the ``session``
fixture, plus the ``PendingProviderSwitch`` consume helpers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from cert_ra.api.domain.accounts.services import ProviderNotPermittedError
from cert_ra.api.lib.pending_provider_switches import (
    PendingProviderSwitchUnusableError,
    assert_pending_provider_switch_usable,
    claim_pending_provider_switch_consumed,
    find_pending_provider_switch_by_token_hash,
)
from cert_ra.api.lib.team_policy import (
    assert_team_provider_allowed,
    find_conflicting_enforcement_users,
    find_stuck_members,
)
from cert_ra.db.models import PendingProviderSwitch, Team
from cert_ra.settings.api import get_feature_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

# Test Team, owned by user@example.com (see tests/data_fixtures.py).
TEST_TEAM_ID = "97108ac1-ffcb-411d-8b1e-d9183399f63b"
OWNER_ID = "5ef29f3c-3560-4d15-ba6b-a2e5c721e4d2"
# test@test.com owns BOTH Simple Team and Extra Team.
MULTI_TEAM_USER_ID = "5ef29f3c-3560-4d15-ba6b-a2e5c721e999"
SIMPLE_TEAM_ID = "81108ac1-ffcb-411d-8b1e-d91833999999"
EXTRA_TEAM_ID = "81108ac1-ffcb-411d-8b1e-d91833999998"


async def _enforce(session: AsyncSession, provider: str | None) -> Team:
    """Set Test Team's enforced_provider in the DB and return the team."""
    team = await session.get(Team, TEST_TEAM_ID)
    assert team is not None
    team.enforced_provider = provider
    team.enforced_provider_set_at = datetime.now(UTC) if provider is not None else None
    await session.commit()
    return team


@pytest.fixture(name="_flag_on")
def fx_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_feature_settings(), "enforced_provider", True)


async def test_assert_noop_when_flag_off(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the flag off, enforcement never raises even on a mismatch."""
    monkeypatch.setattr(get_feature_settings(), "enforced_provider", False)
    await _enforce(session, "google")
    owner = await _get_owner(session)
    # No raise despite the user attempting a different provider.
    await assert_team_provider_allowed(session, owner, attempted_provider="github")


@pytest.mark.usefixtures("_flag_on")
async def test_assert_raises_on_provider_mismatch(session: AsyncSession) -> None:
    """A member signing in via the wrong provider is refused."""
    await _enforce(session, "google")
    owner = await _get_owner(session)
    with pytest.raises(ProviderNotPermittedError) as exc_info:
        await assert_team_provider_allowed(session, owner, attempted_provider="github")
    assert exc_info.value.required_provider == "google"
    assert exc_info.value.attempted_provider == "github"
    assert exc_info.value.target_user_id == owner.id


@pytest.mark.usefixtures("_flag_on")
async def test_assert_allows_matching_provider(session: AsyncSession) -> None:
    """The enforced provider itself is allowed through."""
    await _enforce(session, "google")
    owner = await _get_owner(session)
    await assert_team_provider_allowed(session, owner, attempted_provider="google")


@pytest.mark.usefixtures("_flag_on")
async def test_assert_refuses_password_login_under_enforcement(
    session: AsyncSession,
) -> None:
    """A password attempt (attempted_provider=None) trips any enforcing team."""
    await _enforce(session, "google")
    owner = await _get_owner(session)
    with pytest.raises(ProviderNotPermittedError):
        await assert_team_provider_allowed(session, owner, attempted_provider=None)


async def test_find_stuck_members_empty_without_enforcement(
    session: AsyncSession,
) -> None:
    """No enforcement set → empty stuck list."""
    await _enforce(session, None)
    assert await find_stuck_members(session, TEST_TEAM_ID) == []


async def test_find_stuck_members_lists_unmigrated(session: AsyncSession) -> None:
    """The owner (never signed in via the provider) is stuck after enforcement."""
    await _enforce(session, "google")
    stuck = await find_stuck_members(session, TEST_TEAM_ID)
    emails = {m["email"] for m in stuck}
    assert "user@example.com" in emails


async def test_pending_switch_consume_is_single_use(session: AsyncSession) -> None:
    """The CAS consume claims exactly once; replay loses."""
    row = PendingProviderSwitch(
        target_user_id=OWNER_ID,
        source_provider="google",
        source_subject="sub-123",
        source_email="user@example.com",
        target_provider="microsoft",
        token_hash="deadbeef" * 8,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    session.add(row)
    await session.commit()

    found = await find_pending_provider_switch_by_token_hash(session, "deadbeef" * 8)
    assert found is not None
    assert_pending_provider_switch_usable(found)

    assert await claim_pending_provider_switch_consumed(session, found.id) is True
    assert await claim_pending_provider_switch_consumed(session, found.id) is False

    await session.refresh(found)
    with pytest.raises(PendingProviderSwitchUnusableError):
        assert_pending_provider_switch_usable(found)


async def test_no_conflict_without_enforcement(session: AsyncSession) -> None:
    """No enforcing teams → no conflicts reported."""
    assert await find_conflicting_enforcement_users(session) == []


async def test_detects_multi_team_provider_conflict(session: AsyncSession) -> None:
    """A user owning two teams with different enforced providers is flagged."""
    simple = await session.get(Team, SIMPLE_TEAM_ID)
    extra = await session.get(Team, EXTRA_TEAM_ID)
    assert simple is not None
    assert extra is not None
    simple.enforced_provider = "google"
    extra.enforced_provider = "microsoft"
    await session.commit()

    conflicts = await find_conflicting_enforcement_users(session)
    by_user = {c["userId"]: c for c in conflicts}
    assert MULTI_TEAM_USER_ID in by_user
    assert by_user[MULTI_TEAM_USER_ID]["providers"] == ["google", "microsoft"]


async def test_same_provider_is_not_a_conflict(session: AsyncSession) -> None:
    """Two teams enforcing the SAME provider is fine — not a conflict."""
    simple = await session.get(Team, SIMPLE_TEAM_ID)
    extra = await session.get(Team, EXTRA_TEAM_ID)
    assert simple is not None
    assert extra is not None
    simple.enforced_provider = "google"
    extra.enforced_provider = "google"
    await session.commit()

    conflicts = await find_conflicting_enforcement_users(session)
    assert all(c["userId"] != MULTI_TEAM_USER_ID for c in conflicts)


async def _get_owner(session: AsyncSession):  # noqa: ANN202
    """Load the Test Team owner user row."""
    from cert_ra.db.models import User

    user = await session.get(User, OWNER_ID)
    assert user is not None
    return user
