# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for operator MFA posture (PR-8, Control 1).

Operators (members of ``Team.is_operator``) must sign in with a passkey;
without one the OIDC sign-in handler establishes a partial session
pinned to ``/settings/security/mfa/enroll`` (mirroring the root-account
bootstrap), and TOTP is never accepted as their second factor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cert_ra.api.lib.auth_lockout import (
    OperatorPostureError,
    assert_operator_mfa_posture,
)
from cert_ra.api.lib.operator_roles import (
    is_operator_tenant_admin,
    user_is_operator,
)
from cert_ra.db.models import Team, TeamMember, TeamRoles, User, UserPasskey

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

TEST_TEAM_ID = "97108ac1-ffcb-411d-8b1e-d9183399f63b"
_RANDOM_MEMBER_ID = "00000000-0000-0000-0000-0000000000aa"

OPERATOR_USER_ID = "5ef29f3c-3560-4d15-ba6b-a2e5c721e4d2"  # user@example.com
OPERATOR_EMAIL = "user@example.com"
OPERATOR_PASSWORD = "Test_Password2!"  # noqa: S105


async def _make_operator(
    session: AsyncSession, *, role: TeamRoles = TeamRoles.OPERATOR_SUPPORT
) -> Team:
    """Create an operator team and add the test user to it."""
    team = Team(name="Operator Team", slug="operator-team", is_operator=True)
    session.add(team)
    await session.flush()
    session.add(TeamMember(user_id=OPERATOR_USER_ID, team_id=team.id, role=role))
    await session.commit()
    return team


async def _add_passkey(session: AsyncSession) -> None:
    """Give the operator user a (minimal) enrolled passkey."""
    session.add(
        UserPasskey(
            user_id=OPERATOR_USER_ID,
            credential_id=b"cred-id",
            public_key=b"pub-key",
            sign_count=0,
            device_name="Test Key",
        )
    )
    await session.commit()


async def test_user_is_operator_true_for_member(session: AsyncSession) -> None:
    await _make_operator(session)
    user = await session.get(User, OPERATOR_USER_ID)
    assert user is not None
    assert await user_is_operator(session, user) is True


async def test_posture_raises_for_operator_without_passkey(
    session: AsyncSession,
) -> None:
    await _make_operator(session)
    user = await session.get(User, OPERATOR_USER_ID)
    assert user is not None
    with pytest.raises(OperatorPostureError):
        await assert_operator_mfa_posture(session, user)


async def test_posture_passes_for_operator_with_passkey(
    session: AsyncSession,
) -> None:
    await _make_operator(session)
    await _add_passkey(session)
    user = await session.get(User, OPERATOR_USER_ID)
    assert user is not None
    # Does not raise.
    await assert_operator_mfa_posture(session, user)


async def test_posture_noop_for_non_operator(session: AsyncSession) -> None:
    """A non-operator without a passkey is unaffected."""
    user = await session.get(User, OPERATOR_USER_ID)
    assert user is not None
    await assert_operator_mfa_posture(session, user)


async def test_is_operator_tenant_admin_role(session: AsyncSession) -> None:
    await _make_operator(session, role=TeamRoles.OPERATOR_TENANT_ADMIN)
    user = await session.get(User, OPERATOR_USER_ID)
    assert user is not None
    assert await is_operator_tenant_admin(session, user) is True


async def test_operator_support_is_not_tenant_admin(session: AsyncSession) -> None:
    await _make_operator(session, role=TeamRoles.OPERATOR_SUPPORT)
    user = await session.get(User, OPERATOR_USER_ID)
    assert user is not None
    assert await is_operator_tenant_admin(session, user) is False


# NOTE: the password-login → operator-setup-required routing (AC #26) is
# not integration-tested here. Operator posture is enforced on the OIDC
# sign-in path (operators authenticate via the corporate IdP), which the
# test env does not exercise; the helper-level tests above cover the
# decision logic. The password-path check is deferred (see _access.py).


async def _login(client: AsyncClient, email: str, password: str) -> dict[str, str]:
    """Log in and return headers (XSRF + cookie) for subsequent calls."""
    client.cookies.clear()
    await client.get("/login")
    csrf = client.cookies.get("XSRF-TOKEN") or ""
    await client.post(
        "/login/",
        json={"username": email, "password": password},
        headers={"X-XSRF-TOKEN": csrf, "Content-Type": "application/json"},
        follow_redirects=False,
    )
    csrf = client.cookies.get("XSRF-TOKEN") or csrf
    cookies = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    return {"X-XSRF-TOKEN": csrf, "Content-Type": "application/json", "Cookie": cookies}


async def test_operator_support_can_read_admin_teams(
    client: AsyncClient, session: AsyncSession
) -> None:
    """AC #29: operator_support has read access to admin tooling."""
    await _make_operator(session, role=TeamRoles.OPERATOR_SUPPORT)
    headers = await _login(client, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    resp = await client.get(
        "/admin/teams/",
        headers={**headers, "X-Inertia": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 200


async def test_operator_support_cannot_write(
    client: AsyncClient, session: AsyncSession
) -> None:
    """AC #29: operator_support is refused a cross-customer write (403)."""
    await _make_operator(session, role=TeamRoles.OPERATOR_SUPPORT)
    headers = await _login(client, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    resp = await client.post(
        f"/admin/teams/{TEST_TEAM_ID}/members/{_RANDOM_MEMBER_ID}/force-unlock/",
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code == 403


async def test_operator_tenant_admin_passes_write_guard(
    client: AsyncClient, session: AsyncSession
) -> None:
    """AC #29: operator_tenant_admin clears the write guard (not 403)."""
    await _make_operator(session, role=TeamRoles.OPERATOR_TENANT_ADMIN)
    headers = await _login(client, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    resp = await client.post(
        f"/admin/teams/{TEST_TEAM_ID}/members/{_RANDOM_MEMBER_ID}/force-unlock/",
        headers=headers,
        follow_redirects=False,
    )
    # Guard passes; the missing member yields a non-403 outcome.
    assert resp.status_code != 403
