# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Onboarding: password-set activation + ownership transfer.

Covers the admin-provisioning onboarding choice (an invitee sets a
password instead of being forced through OIDC) and the operator
ownership-transfer action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.teams.controllers._team_invitation import _provision_invitee
from cert_ra.api.domain.teams.services import TeamMemberService
from cert_ra.api.lib.token_hashing import hmac_sha256
from cert_ra.db.models import (
    Team,
    TeamInvitation,
    TeamMember,
    TeamRoles,
    User,
)
from cert_ra.db.models.team_invitation import InvitationKind

if TYPE_CHECKING:
    from uuid import UUID

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

_TOKEN = "onboarding-activation-token-abc123"  # noqa: S105
_GOOD_PW = "BrandNewPass123!"


@dataclass
class Provisioned:
    """Plain-value handles for a provisioned invitee (no ORM attribute access)."""

    user_id: UUID
    email: str
    invitation_id: UUID


async def _csrf_headers(client: AsyncClient) -> dict[str, str]:
    await client.get("/login")
    csrf = client.cookies.get("XSRF-TOKEN") or ""
    return {"X-XSRF-TOKEN": csrf, "Content-Type": "application/json"}


async def _provision(
    session: AsyncSession,
    *,
    slug: str,
    enforced_provider: str | None = None,
    activated: bool = False,
    token: str = _TOKEN,
) -> Provisioned:
    """Create a pre-provisioned (no-password) user + team + invitation."""
    email = f"invitee-{slug}@example.com"
    user = User(
        email=email,
        is_active=True,
        is_verified=False,
        activated_at=datetime.now(UTC) if activated else None,
    )
    session.add(user)
    await session.flush()
    team = Team(name=f"Org {slug}", slug=slug, enforced_provider=enforced_provider)
    session.add(team)
    await session.flush()
    session.add(
        TeamMember(
            team_id=team.id, user_id=user.id, role=TeamRoles.MEMBER, is_owner=False
        )
    )
    invitation = TeamInvitation(
        team_id=team.id,
        email=email,
        role=TeamRoles.MEMBER,
        invited_by_email="operator@certora.com",
        token_hash=hmac_sha256(token),
        kind=InvitationKind.FIRST_TIME_ACTIVATION,
        user_id=user.id,
        is_accepted=False,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    session.add(invitation)
    await session.flush()
    handle = Provisioned(user_id=user.id, email=email, invitation_id=invitation.id)
    await session.commit()
    return handle


async def _user_state(
    session: AsyncSession, user_id: UUID
) -> tuple[datetime | None, str | None]:
    """``(activated_at, hashed_password)`` read by explicit column select."""
    row = (
        await session.execute(
            select(User.activated_at, User.hashed_password).where(User.id == user_id)
        )
    ).one()
    return (row.activated_at, row.hashed_password)


async def test_activation_page_offers_password_and_oidc(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A pre-provisioned invitee (no enforced provider) gets the choice."""
    await _provision(session, slug="act-choice")
    resp = await client.get(f"/invitations/{_TOKEN}/", headers={"X-Inertia": "true"})
    assert resp.status_code == 200
    content = resp.json()["props"]["content"]
    assert content["isActivation"] is True
    assert content["allowPassword"] is True
    assert [o["provider"] for o in content["oidcOptions"]] == [
        "google",
        "microsoft",
        "github",
    ]


async def test_activation_page_redirects_to_oidc_when_enforced(
    client: AsyncClient, session: AsyncSession
) -> None:
    """When the team enforces a provider, the link goes straight to it."""
    await _provision(session, slug="act-enforced", enforced_provider="google")
    resp = await client.get(f"/invitations/{_TOKEN}/", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert "/auth/google/login" in resp.headers.get("location", "")


async def test_set_password_activates_account(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Setting a password activates the user and accepts the invitation."""
    handle = await _provision(session, slug="act-setpw")
    headers = await _csrf_headers(client)
    resp = await client.post(
        f"/invitations/{_TOKEN}/set-password",
        json={"password": _GOOD_PW, "confirm_password": _GOOD_PW},
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303, 307)
    assert "/dashboard" in resp.headers.get("location", "")

    session.expire_all()
    activated_at, hashed = await _user_state(session, handle.user_id)
    assert activated_at is not None
    assert hashed is not None
    accepted = await session.scalar(
        select(TeamInvitation.is_accepted).where(
            TeamInvitation.id == handle.invitation_id
        )
    )
    assert accepted is True


async def test_set_password_refused_when_enforced(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Password activation is blocked once the team enforces a provider."""
    handle = await _provision(session, slug="act-refused", enforced_provider="google")
    headers = await _csrf_headers(client)
    resp = await client.post(
        f"/invitations/{_TOKEN}/set-password",
        json={"password": _GOOD_PW, "confirm_password": _GOOD_PW},
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303, 307)
    assert "/invitations/" in resp.headers.get("location", "")

    session.expire_all()
    activated_at, hashed = await _user_state(session, handle.user_id)
    assert hashed is None
    assert activated_at is None


async def test_set_password_rejects_weak_password(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Too-short or mismatched passwords are rejected; no activation."""
    handle = await _provision(session, slug="act-weak")
    headers = await _csrf_headers(client)
    resp = await client.post(
        f"/invitations/{_TOKEN}/set-password",
        json={"password": "short", "confirm_password": "short"},
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303, 307)
    session.expire_all()
    _, hashed = await _user_state(session, handle.user_id)
    assert hashed is None


async def test_set_password_already_activated_is_rejected(
    client: AsyncClient, session: AsyncSession
) -> None:
    """An already-activated account can't be re-activated via the link."""
    handle = await _provision(session, slug="act-already", activated=True)
    headers = await _csrf_headers(client)
    resp = await client.post(
        f"/invitations/{_TOKEN}/set-password",
        json={"password": _GOOD_PW, "confirm_password": _GOOD_PW},
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303, 307)
    assert "/login" in resp.headers.get("location", "")
    session.expire_all()
    _, hashed = await _user_state(session, handle.user_id)
    assert hashed is None


async def test_make_owner_transfers_ownership(
    client: AsyncClient,
    session: AsyncSession,
    superuser_inertia_headers: dict[str, str],
) -> None:
    """Operator transfers ownership: new owner promoted, old owner demoted."""
    owner = User(email="owner-a@example.com", is_active=True, is_verified=True)
    other = User(email="member-b@example.com", is_active=True, is_verified=True)
    session.add_all([owner, other])
    await session.flush()
    team = Team(name="Transfer Org", slug="transfer-org")
    session.add(team)
    await session.flush()
    member_a = TeamMember(
        team_id=team.id, user_id=owner.id, role=TeamRoles.ADMIN, is_owner=True
    )
    member_b = TeamMember(
        team_id=team.id, user_id=other.id, role=TeamRoles.MEMBER, is_owner=False
    )
    session.add_all([member_a, member_b])
    await session.flush()
    # Capture plain ids before commit so assertions never touch the ORM objects.
    team_id, member_a_id, member_b_id = team.id, member_a.id, member_b.id
    await session.commit()

    resp = await client.post(
        f"/admin/teams/{team_id}/members/{member_b_id}/make-owner/",
        headers=superuser_inertia_headers,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303, 307)

    session.expire_all()
    new_owner = (
        await session.execute(
            select(TeamMember.is_owner, TeamMember.role).where(
                TeamMember.id == member_b_id
            )
        )
    ).one()
    old_is_owner = await session.scalar(
        select(TeamMember.is_owner).where(TeamMember.id == member_a_id)
    )
    assert new_owner.is_owner is True
    assert new_owner.role == TeamRoles.ADMIN
    assert old_is_owner is False


async def test_owner_add_unactivated_user_sends_activation_invite(
    client: AsyncClient,
    session: AsyncSession,
    superuser_token_headers: dict[str, str],
) -> None:
    """Adding an existing-but-never-activated email invites, not dead-adds."""
    team = Team(name="Owner Add Org", slug="owner-add-org")
    session.add(team)
    await session.flush()
    # An account that exists but was never activated (e.g. operator
    # pre-provisioned for SSO, never completed): no password, activated_at NULL.
    ghost = User(
        email="ghost@example.com",
        is_active=True,
        is_verified=False,
        activated_at=None,
    )
    session.add(ghost)
    await session.flush()
    team_id, team_slug, ghost_id = team.id, team.slug, ghost.id
    await session.commit()

    resp = await client.post(
        f"/api/teams/{team_slug}/members/add",
        json={"userName": "ghost@example.com"},
        headers=superuser_token_headers,
    )
    assert resp.status_code in (200, 201), resp.text

    session.expire_all()
    membership = await session.scalar(
        select(TeamMember.id).where(
            TeamMember.team_id == team_id, TeamMember.user_id == ghost_id
        )
    )
    assert membership is not None
    invite = (
        await session.execute(
            select(TeamInvitation.kind, TeamInvitation.user_id).where(
                TeamInvitation.team_id == team_id,
                TeamInvitation.email == "ghost@example.com",
            )
        )
    ).first()
    assert invite is not None
    assert invite.kind == InvitationKind.FIRST_TIME_ACTIVATION
    assert invite.user_id == ghost_id


async def test_owner_add_activated_user_adds_directly(
    client: AsyncClient,
    session: AsyncSession,
    superuser_token_headers: dict[str, str],
) -> None:
    """An already-activated colleague is added directly, with no invite."""
    team = Team(name="Direct Add Org", slug="direct-add-org")
    session.add(team)
    await session.flush()
    active = User(
        email="active@example.com",
        is_active=True,
        is_verified=True,
        activated_at=datetime.now(UTC),
    )
    session.add(active)
    await session.flush()
    team_id, team_slug = team.id, team.slug
    await session.commit()

    resp = await client.post(
        f"/api/teams/{team_slug}/members/add",
        json={"userName": "active@example.com"},
        headers=superuser_token_headers,
    )
    assert resp.status_code in (200, 201), resp.text

    session.expire_all()
    invite_count = await session.scalar(
        select(func.count())
        .select_from(TeamInvitation)
        .where(
            TeamInvitation.team_id == team_id,
            TeamInvitation.email == "active@example.com",
        )
    )
    assert invite_count == 0


async def test_remove_owner_is_rejected(
    client: AsyncClient,
    session: AsyncSession,
    superuser_token_headers: dict[str, str],
) -> None:
    """The team owner can't be removed without transferring ownership first."""
    owner = User(email="sole-owner@example.com", is_active=True, is_verified=True)
    session.add(owner)
    await session.flush()
    team = Team(name="Sole Owner Org", slug="sole-owner-org")
    session.add(team)
    await session.flush()
    membership = TeamMember(
        team_id=team.id, user_id=owner.id, role=TeamRoles.ADMIN, is_owner=True
    )
    session.add(membership)
    await session.flush()
    team_slug, member_id = team.slug, membership.id
    await session.commit()

    resp = await client.post(
        f"/api/teams/{team_slug}/members/remove",
        json={"userName": "sole-owner@example.com"},
        headers=superuser_token_headers,
    )
    assert resp.status_code == 400

    session.expire_all()
    still_there = await session.scalar(
        select(TeamMember.id).where(TeamMember.id == member_id)
    )
    assert still_there is not None


async def test_provision_invitee_creates_user_for_new_email(
    session: AsyncSession,
) -> None:
    """A brand-new invitee is pre-provisioned with a User row + membership.

    Regression: the team-invitations endpoint used to create invitations
    without a ``user_id``, so invitees were never shown the set-password card.
    ``_provision_invitee`` must mint the User, attach the membership, and
    report FIRST_TIME_ACTIVATION so the invitation carries a ``user_id``.
    """
    inviter = User(email="inviter-new@example.com", is_active=True, is_verified=True)
    session.add(inviter)
    team = Team(name="Provision New Org", slug="provision-new-org")
    session.add(team)
    await session.flush()
    await session.commit()

    invitee, kind = await _provision_invitee(
        users_service=UserService(session=session),
        team_members_service=TeamMemberService(session=session),
        team=team,
        normalized_email="fresh@example.com",
        role=TeamRoles.MEMBER,
        inviter=inviter,
    )
    await session.commit()

    assert kind == InvitationKind.FIRST_TIME_ACTIVATION
    assert invitee.email == "fresh@example.com"
    activated_at, hashed = await _user_state(session, invitee.id)
    assert activated_at is None
    assert hashed is None
    membership = await session.scalar(
        select(TeamMember.id).where(
            TeamMember.team_id == team.id, TeamMember.user_id == invitee.id
        )
    )
    assert membership is not None


async def test_provision_invitee_reuses_unactivated_user(
    session: AsyncSession,
) -> None:
    """An existing-but-never-activated user is reused and gets a membership."""
    inviter = User(email="inviter-ghost@example.com", is_active=True, is_verified=True)
    session.add(inviter)
    ghost = User(
        email="ghost-invite@example.com",
        is_active=True,
        is_verified=False,
        activated_at=None,
    )
    session.add(ghost)
    team = Team(name="Provision Ghost Org", slug="provision-ghost-org")
    session.add(team)
    await session.flush()
    ghost_id = ghost.id
    await session.commit()

    invitee, kind = await _provision_invitee(
        users_service=UserService(session=session),
        team_members_service=TeamMemberService(session=session),
        team=team,
        normalized_email="ghost-invite@example.com",
        role=TeamRoles.MEMBER,
        inviter=inviter,
    )
    await session.commit()

    assert kind == InvitationKind.FIRST_TIME_ACTIVATION
    assert invitee.id == ghost_id  # reused, not duplicated
    membership = await session.scalar(
        select(TeamMember.id).where(
            TeamMember.team_id == team.id, TeamMember.user_id == ghost_id
        )
    )
    assert membership is not None


async def test_provision_invitee_cross_team_join_for_activated_user(
    session: AsyncSession,
) -> None:
    """An already-activated user yields CROSS_TEAM_JOIN and no pre-membership.

    They accept while signed in, so the membership is created on accept — not
    here — and the password step is skipped (they already have a login).
    """
    inviter = User(email="inviter-x@example.com", is_active=True, is_verified=True)
    session.add(inviter)
    active = User(
        email="active-invite@example.com",
        is_active=True,
        is_verified=True,
        activated_at=datetime.now(UTC),
    )
    session.add(active)
    team = Team(name="Provision Active Org", slug="provision-active-org")
    session.add(team)
    await session.flush()
    active_id = active.id
    await session.commit()

    invitee, kind = await _provision_invitee(
        users_service=UserService(session=session),
        team_members_service=TeamMemberService(session=session),
        team=team,
        normalized_email="active-invite@example.com",
        role=TeamRoles.MEMBER,
        inviter=inviter,
    )
    await session.commit()

    assert kind == InvitationKind.CROSS_TEAM_JOIN
    assert invitee.id == active_id
    membership = await session.scalar(
        select(TeamMember.id).where(
            TeamMember.team_id == team.id, TeamMember.user_id == active_id
        )
    )
    assert membership is None  # created on accept, not at invite time
