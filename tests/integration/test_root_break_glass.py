# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for the break-glass root account (PR-8 follow-up).

The root (``CERT_RA_SUPERUSER_EMAIL``) is exempt from operator IDP
enforcement, can never sign in via an IdP, and on first login must
rotate its seeded password and (then) have a passkey.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import select

from cert_ra.api.domain.accounts.services import (
    OidcIdentityResolver,
    ProviderNotPermittedError,
    RootCannotUseIdpError,
)
from cert_ra.api.lib import crypt
from cert_ra.api.lib.oidc.identity import ExtractedIdentity
from cert_ra.api.lib.oidc.providers import Provider
from cert_ra.api.lib.team_policy import assert_team_provider_allowed
from cert_ra.db.models import Team, TeamMember, TeamRoles, User
from cert_ra.settings.api import get_superuser_settings

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

ROOT_EMAIL = get_superuser_settings().email  # user@certora.com
NON_ROOT_OP_ID = "5ef29f3c-3560-4d15-ba6b-a2e5c721e999"  # test@test.com
_OLD_PW = "OldPassw0rd!!"


async def _make_root(session: AsyncSession, *, must_change: bool = False) -> User:
    user = User(
        email=ROOT_EMAIL,
        hashed_password=await crypt.get_password_hash(_OLD_PW),
        is_superuser=True,
        is_active=True,
        is_verified=True,
        must_change_password=must_change,
    )
    session.add(user)
    await session.flush()
    return user


async def _operator_team(session: AsyncSession, *, provider: str) -> Team:
    team = Team(
        name="Op BG", slug="op-bg", is_operator=True, enforced_provider=provider
    )
    session.add(team)
    await session.flush()
    return team


async def _login_headers(client: AsyncClient) -> dict[str, str]:
    await client.get("/login")
    csrf = client.cookies.get("XSRF-TOKEN") or ""
    return {"X-XSRF-TOKEN": csrf, "Content-Type": "application/json"}


async def test_root_exempt_from_operator_enforcement(session: AsyncSession) -> None:
    """The root can password-login even when its operator team enforces an IdP."""
    root = await _make_root(session)
    team = await _operator_team(session, provider="google")
    session.add(
        TeamMember(
            user_id=root.id, team_id=team.id, role=TeamRoles.OPERATOR_TENANT_ADMIN
        )
    )
    await session.commit()
    # attempted_provider=None (password) must NOT raise for the root.
    await assert_team_provider_allowed(session, root, attempted_provider=None)


async def test_operator_enforcement_always_on_for_non_root(
    session: AsyncSession,
) -> None:
    """Operator-team enforcement applies even with the customer flag off."""
    team = await _operator_team(session, provider="google")
    session.add(
        TeamMember(
            user_id=UUID(NON_ROOT_OP_ID),
            team_id=team.id,
            role=TeamRoles.OPERATOR_SUPPORT,
        )
    )
    await session.commit()
    user = await session.get(User, NON_ROOT_OP_ID)
    assert user is not None
    with pytest.raises(ProviderNotPermittedError):
        await assert_team_provider_allowed(session, user, attempted_provider=None)


async def test_root_oidc_sign_in_refused(session: AsyncSession) -> None:
    """The root can never resolve via an IdP handshake."""
    await _make_root(session)
    await session.commit()
    resolver = OidcIdentityResolver(session)
    identity = ExtractedIdentity(
        provider=Provider.GOOGLE, subject="sub-root", email=ROOT_EMAIL, name="Root"
    )
    with pytest.raises(RootCannotUseIdpError):
        await resolver.resolve(identity)


async def test_root_first_login_forces_password_change(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Root with must_change_password is sent to the forced-change page."""
    await _make_root(session, must_change=True)
    await session.commit()
    headers = await _login_headers(client)
    resp = await client.post(
        "/login/",
        json={"username": ROOT_EMAIL, "password": _OLD_PW},
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303, 307)
    assert "force-password-change" in resp.headers.get("location", "")


async def test_force_password_change_clears_flag(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Submitting the forced change updates the password and clears the flag."""
    await _make_root(session, must_change=True)
    await session.commit()
    headers = await _login_headers(client)
    # Login sets the force_password_change_user_id session marker.
    await client.post(
        "/login/",
        json={"username": ROOT_EMAIL, "password": _OLD_PW},
        headers=headers,
        follow_redirects=False,
    )
    csrf = client.cookies.get("XSRF-TOKEN") or headers["X-XSRF-TOKEN"]
    resp = await client.post(
        "/auth/force-password-change/",
        json={
            "password": "BrandNewPass123!",
            "confirm_password": "BrandNewPass123!",
        },
        headers={"X-XSRF-TOKEN": csrf, "Content-Type": "application/json"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303, 307)
    session.expire_all()
    root = await session.scalar(select(User).where(User.email == ROOT_EMAIL))
    assert root is not None
    assert root.must_change_password is False


async def test_root_without_passkey_routed_to_enrollment(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Root with no passkey is routed to MFA enrollment, not the dead-end page.

    Narrow bootstrap weakening of Control 1 — scoped to the break-glass
    identity — so the very first root login has an in-app path to enroll
    a passkey. The OIDC operator path still dead-ends.
    """
    await _make_root(session, must_change=False)
    await session.commit()
    headers = await _login_headers(client)
    resp = await client.post(
        "/login/",
        json={"username": ROOT_EMAIL, "password": _OLD_PW},
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303, 307)
    location = resp.headers.get("location", "")
    assert "/settings/security/mfa/enroll" in location
    assert "/auth/operator-setup-required" not in location
