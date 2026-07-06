# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from advanced_alchemy.exceptions import RepositoryError
from sqlalchemy import delete, select, update

from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.teams.services import (
    TeamInvitationService,
    TeamMemberService,
    TeamService,
)
from cert_ra.db.models import Team, TeamMember, TeamRoles, User
from cert_ra.db.models.audit_log import AuditAction, AuditLog
from cert_ra.settings.api import OperatorTeamSettings, get_operator_team_settings

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SUPERUSER_ID = "97108ac1-ffcb-411d-8b1e-d9183399f63b"
USER_ID = "5ef29f3c-3560-4d15-ba6b-a2e5c721e4d2"

pytestmark = pytest.mark.anyio


async def test_teams_with_no_auth(client: "AsyncClient") -> None:
    """Unauthenticated requests should be rejected or redirect to login.

    Routes with Inertia components redirect to login (303 for non-GET, 307 for GET).
    Routes without components return 401.
    """
    # PATCH with component="team/edit" redirects to login (303 See Other)
    response = await client.patch("/teams/test-team/", json={"name": "TEST UPDATE"})
    assert response.status_code == 303

    # POST without component returns 401 (not authorized)
    response = await client.post(
        "/teams",
        json={"name": "A User", "email": "new-user@example.com", "password": "S3cret!"},
    )
    assert response.status_code == 401

    # DELETE without component returns 401 (status_code=303 only applies to success)
    response = await client.delete("/teams/test-team/")
    assert response.status_code == 401

    # GET with component="team/show" redirects to login (307 Temporary Redirect)
    response = await client.get("/teams/test-team/")
    assert response.status_code == 307
    # GET with component="team/list" redirects to login (307 Temporary Redirect)
    response = await client.get("/teams")
    assert response.status_code == 307


async def test_teams_with_incorrect_role(
    client: "AsyncClient", user_inertia_headers: dict[str, str]
) -> None:
    """User without team access should be redirected for unauthorized team operations.

    Note: Inertia uses 307 for GET (preserves method) and 303 for other methods.
    """
    # User cannot update a team they don't have admin access to
    response = await client.patch(
        "/teams/simple-team/",
        json={"name": "TEST UPDATE"},
        headers=user_inertia_headers,
    )
    assert response.status_code == 303  # PATCH redirects to GET via 303

    # User can create a new team (they become the owner)
    response = await client.post(
        "/teams",
        json={"name": "A new team."},
        headers=user_inertia_headers,
    )
    assert response.status_code == 303  # POST redirects to GET via 303

    # User cannot view a team they're not a member of
    response = await client.get("/teams/simple-team/", headers=user_inertia_headers)
    assert response.status_code == 307  # GET preserves method via 307

    # User can view the teams list (filtered to their teams)
    response = await client.get("/teams", headers=user_inertia_headers)
    assert response.status_code == 200

    # User cannot delete a team they don't own
    response = await client.delete("/teams/simple-team/", headers=user_inertia_headers)
    assert response.status_code == 303  # DELETE redirects to GET via 303


async def test_teams_list(
    client: "AsyncClient", superuser_inertia_headers: dict[str, str]
) -> None:
    """Superuser can list all teams via Inertia endpoint."""
    response = await client.get("/teams", headers=superuser_inertia_headers)
    assert response.status_code == 200
    data = response.json()
    # Inertia responses wrap handler return value in props.content
    assert "props" in data
    content = data["props"]["content"]
    assert "teams" in content
    assert len(content["teams"]) > 0
    # Verify team structure includes role information
    team = content["teams"][0]
    assert "id" in team
    assert "name" in team
    assert "userRole" in team
    assert "memberCount" in team


async def test_teams_get(
    client: "AsyncClient", superuser_inertia_headers: dict[str, str]
) -> None:
    """Superuser can get a specific team via Inertia endpoint."""
    response = await client.get("/teams/test-team/", headers=superuser_inertia_headers)
    assert response.status_code == 200
    data = response.json()
    # Inertia responses wrap handler return value in props.content
    assert "props" in data
    content = data["props"]["content"]
    assert "team" in content
    assert content["team"]["name"] == "Test Team"
    # Verify permissions are included
    assert "permissions" in content
    assert "canUpdateTeam" in content["permissions"]
    # Verify members are included
    assert "members" in content


async def test_teams_create(
    client: "AsyncClient", superuser_inertia_headers: dict[str, str]
) -> None:
    """Superuser can create a team."""
    response = await client.post(
        "/teams",
        json={"name": "My First Team", "tags": ["cool tag"]},
        headers=superuser_inertia_headers,
    )
    # Inertia create returns a redirect (303)
    assert response.status_code == 303


async def test_teams_update(
    client: "AsyncClient", superuser_inertia_headers: dict[str, str]
) -> None:
    """Superuser can update a team via Inertia endpoint."""
    response = await client.patch(
        "/teams/test-team/",
        json={"name": "Name Changed"},
        headers=superuser_inertia_headers,
    )
    assert response.status_code == 200


async def test_teams_delete(
    client: "AsyncClient",
    superuser_inertia_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
) -> None:
    """Superuser can delete a team."""
    response = await client.delete(
        "/teams/simple-team/",
        headers=superuser_inertia_headers,
    )
    # Inertia delete returns a redirect (303)
    assert response.status_code == 303
    # Ensure we didn't cascade delete the users that were members of the team
    response = await client.get(
        "/api/users/5ef29f3c-3560-4d15-ba6b-a2e5c721e999",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200


async def test_team_member_requires_admin(
    client: "AsyncClient",
    user_token_headers: dict[str, str],
) -> None:
    """Non-admin users cannot add members to teams they don't manage."""
    response = await client.post(
        "/api/teams/simple-team/members/add",
        json={"userName": "another@example.com"},
        headers=user_token_headers,
    )
    assert response.status_code == 403


async def test_team_member_remove_only_target_team(
    client: "AsyncClient",
    superuser_token_headers: dict[str, str],
    superuser_inertia_headers: dict[str, str],
) -> None:
    """Removing a member from one team should not remove them from others."""
    response = await client.post(
        "/api/teams/simple-team/members/add",
        json={"userName": "user@example.com"},
        headers=superuser_token_headers,
    )
    assert response.status_code == 201
    response = await client.post(
        "/api/teams/extra-team/members/add",
        json={"userName": "user@example.com"},
        headers=superuser_token_headers,
    )
    assert response.status_code == 201

    response = await client.post(
        "/api/teams/simple-team/members/remove",
        json={"userName": "user@example.com"},
        headers=superuser_token_headers,
    )
    assert response.status_code == 200

    response = await client.get("/teams/extra-team/", headers=superuser_inertia_headers)
    assert response.status_code == 200
    extra_members = response.json()["props"]["content"]["members"]
    assert any(member["email"] == "user@example.com" for member in extra_members)

    response = await client.get(
        "/teams/simple-team/", headers=superuser_inertia_headers
    )
    assert response.status_code == 200
    simple_members = response.json()["props"]["content"]["members"]
    assert all(member["email"] != "user@example.com" for member in simple_members)


async def test_remove_from_only_team_auto_deletes_user(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    superuser_token_headers: dict[str, str],
) -> None:
    """A user removed from their only team is purged from the database."""
    target_email = "auto-delete@example.com"

    response = await client.post(
        "/api/teams/simple-team/members/add",
        json={"userName": target_email},
        headers=superuser_token_headers,
    )
    assert response.status_code == 201

    async with sessionmaker() as session:
        before = await session.scalar(select(User).where(User.email == target_email))
        assert before is not None

    response = await client.post(
        "/api/teams/simple-team/members/remove",
        json={"userName": target_email},
        headers=superuser_token_headers,
    )
    assert response.status_code == 200

    async with sessionmaker() as session:
        after = await session.scalar(select(User).where(User.email == target_email))
        assert after is None


async def test_remove_superuser_from_only_team_keeps_user(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    superuser_token_headers: dict[str, str],
) -> None:
    """Superusers are never auto-deleted, even when removed from their last team."""
    response = await client.post(
        "/api/teams/simple-team/members/add",
        json={"userName": "superuser@example.com"},
        headers=superuser_token_headers,
    )
    assert response.status_code == 201

    response = await client.post(
        "/api/teams/simple-team/members/remove",
        json={"userName": "superuser@example.com"},
        headers=superuser_token_headers,
    )
    assert response.status_code == 200

    async with sessionmaker() as session:
        survivor = await session.scalar(
            select(User).where(User.email == "superuser@example.com")
        )
        assert survivor is not None
        assert survivor.is_superuser is True


async def test_admin_team_delete_purges_orphaned_members(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    superuser_token_headers: dict[str, str],
    superuser_inertia_headers: dict[str, str],
) -> None:
    """Deleting a team auto-deletes members for whom this was the only team."""
    target_email = "team-delete-orphan@example.com"
    response = await client.post(
        "/api/teams/simple-team/members/add",
        json={"userName": target_email},
        headers=superuser_token_headers,
    )
    assert response.status_code == 201

    response = await client.delete(
        "/admin/teams/81108ac1-ffcb-411d-8b1e-d91833999999/",
        headers=superuser_inertia_headers,
    )
    assert response.status_code == 303

    async with sessionmaker() as session:
        orphan = await session.scalar(select(User).where(User.email == target_email))
        assert orphan is None
        # test@test.com also owned extra-team, so they survive the cascade.
        surviving_owner = await session.scalar(
            select(User).where(User.email == "test@test.com")
        )
        assert surviving_owner is not None


async def test_team_delete_skips_actor(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    user_inertia_headers: dict[str, str],
) -> None:
    """An owner deleting their own only team is not auto-deleted themselves."""
    response = await client.delete(
        "/teams/test-team/",
        headers=user_inertia_headers,
    )
    assert response.status_code == 303

    async with sessionmaker() as session:
        actor = await session.scalar(
            select(User).where(User.email == "user@example.com")
        )
        assert actor is not None


async def test_admin_remove_member_audits_user_delete(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    superuser_token_headers: dict[str, str],
    superuser_inertia_headers: dict[str, str],
) -> None:
    """Admin remove that orphans a user emits a USER_DELETED audit entry."""
    target_email = "audited-orphan@example.com"
    response = await client.post(
        "/api/teams/simple-team/members/add",
        json={"userName": target_email},
        headers=superuser_token_headers,
    )
    assert response.status_code == 201

    async with sessionmaker() as session:
        member = await session.scalar(
            select(TeamMember)
            .join(User, TeamMember.user_id == User.id)
            .where(User.email == target_email)
        )
        assert member is not None
        member_id = member.id
        team_id = member.team_id
        user_id = member.user_id

    response = await client.delete(
        f"/admin/teams/{team_id}/members/{member_id}/",
        headers=superuser_inertia_headers,
    )
    assert response.status_code == 303

    async with sessionmaker() as session:
        deleted_entry = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.USER_DELETED,
                AuditLog.target_id == user_id,
            )
        )
        assert deleted_entry is not None
        assert deleted_entry.target_label == target_email


async def test_invitation_cancel_team_mismatch(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    superuser_inertia_headers: dict[str, str],
) -> None:
    """Invitation cancellation should verify the invitation belongs to the team slug."""
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        user_service = UserService(session=session)
        invitation_service = TeamInvitationService(session=session)
        team = await team_service.get_one(slug="simple-team")
        inviter = await user_service.get_one(email="superuser@example.com")
        invitation, _ = await invitation_service.create_invitation(
            team=team,
            email="another@example.com",
            role=TeamRoles.MEMBER,
            invited_by=inviter,
        )
        await invitation_service.repository.session.commit()
        invitation_id = invitation.id

    response = await client.delete(
        f"/teams/test-team/invitations/{invitation_id}",
        headers=superuser_inertia_headers,
    )
    assert response.status_code == 303

    async with sessionmaker() as session:
        invitation_service = TeamInvitationService(session=session)
        stored = await invitation_service.get_one_or_none(id=invitation_id)
        assert stored is not None


# ---------------------------------------------------------------------------
# Team domain restriction
# ---------------------------------------------------------------------------


async def test_team_create_normalizes_domain(
    sessionmaker: "async_sessionmaker[AsyncSession]",
) -> None:
    """Whitespace, leading ``@`` and uppercase characters are normalized."""
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        team = await team_service.create(
            {
                "name": "Domainy Team",
                "owner_id": SUPERUSER_ID,
                "domain": "  @CERTORA.com ",
            }
        )
        await team_service.repository.session.commit()
        assert team.domain == "certora.com"


async def test_team_create_invalid_domain_rejected(
    sessionmaker: "async_sessionmaker[AsyncSession]",
) -> None:
    """A malformed domain is rejected at create time."""
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        with pytest.raises(RepositoryError):
            await team_service.create(
                {
                    "name": "Bad Domain Team",
                    "owner_id": SUPERUSER_ID,
                    "domain": "not a domain",
                }
            )


async def test_team_create_no_domain_stores_none(
    sessionmaker: "async_sessionmaker[AsyncSession]",
) -> None:
    """Omitting domain leaves it ``None`` so any email may be invited."""
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        team = await team_service.create(
            {"name": "No Domain Team", "owner_id": SUPERUSER_ID}
        )
        assert team.domain is None


async def test_team_update_does_not_change_domain(
    sessionmaker: "async_sessionmaker[AsyncSession]",
) -> None:
    """The domain is set-once on create and ignored on update."""
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        team = await team_service.create(
            {
                "name": "Locked Domain Team",
                "owner_id": SUPERUSER_ID,
                "domain": "example.com",
            }
        )
        await team_service.repository.session.commit()
        team_id = team.id

    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        await team_service.update(
            item_id=team_id, data={"domain": "evil.com", "description": "updated"}
        )
        await team_service.repository.session.commit()

    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        refreshed = await team_service.get(team_id)
        assert refreshed.domain == "example.com"
        assert refreshed.description == "updated"


async def test_create_team_via_endpoint_with_domain(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    superuser_inertia_headers: dict[str, str],
) -> None:
    """The HTTP create endpoint accepts and stores the team's domain."""
    response = await client.post(
        "/teams",
        json={"name": "Endpoint Domain Team", "domain": "Example.COM"},
        headers=superuser_inertia_headers,
    )
    assert response.status_code == 303

    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        team = await team_service.get_one(slug="endpoint-domain-team")
        assert team.domain == "example.com"


async def _create_team_with_domain(
    sessionmaker: "async_sessionmaker[AsyncSession]",
    *,
    name: str,
    slug: str,
    domain: str | None,
    owner_id: str = SUPERUSER_ID,
) -> None:
    """Seed a team with a specific domain for the test that follows."""
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        await team_service.create(
            {"name": name, "slug": slug, "owner_id": owner_id, "domain": domain}
        )
        await team_service.repository.session.commit()


async def test_invite_with_matching_domain_succeeds(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    superuser_inertia_headers: dict[str, str],
) -> None:
    """An invite whose email matches the team domain is created."""
    await _create_team_with_domain(
        sessionmaker, name="Domain Team", slug="domain-team", domain="example.com"
    )

    response = await client.post(
        "/teams/domain-team/invitations/",
        json={"email": "another@example.com", "role": "member"},
        headers=superuser_inertia_headers,
    )
    assert response.status_code == 303

    async with sessionmaker() as session:
        invitation_service = TeamInvitationService(session=session)
        invites = await invitation_service.list()
        assert any(inv.email == "another@example.com" for inv in invites)


async def test_invite_with_mismatched_domain_rejected(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    superuser_inertia_headers: dict[str, str],
) -> None:
    """An invite whose email does not match the team domain is dropped."""
    await _create_team_with_domain(
        sessionmaker, name="Domain Team", slug="domain-team", domain="example.com"
    )

    response = await client.post(
        "/teams/domain-team/invitations/",
        json={"email": "outsider@other.com", "role": "member"},
        headers=superuser_inertia_headers,
    )
    # The handler flashes an error and redirects rather than raising.
    assert response.status_code == 303

    async with sessionmaker() as session:
        invitation_service = TeamInvitationService(session=session)
        invites = await invitation_service.list()
        assert all(inv.email != "outsider@other.com" for inv in invites)


async def test_invite_with_uppercase_email_matches_domain(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    superuser_inertia_headers: dict[str, str],
) -> None:
    """The domain check is case-insensitive on the invitee email side."""
    await _create_team_with_domain(
        sessionmaker, name="Domain Team", slug="domain-team", domain="example.com"
    )

    response = await client.post(
        "/teams/domain-team/invitations/",
        json={"email": "Mixed@Example.COM", "role": "member"},
        headers=superuser_inertia_headers,
    )
    assert response.status_code == 303

    async with sessionmaker() as session:
        invitation_service = TeamInvitationService(session=session)
        invites = await invitation_service.list()
        assert any(inv.email == "Mixed@Example.COM" for inv in invites)


async def test_invite_to_team_without_domain_unrestricted(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    superuser_inertia_headers: dict[str, str],
) -> None:
    """A team with no domain accepts invites from any email domain."""
    await _create_team_with_domain(
        sessionmaker, name="Open Team", slug="open-team", domain=None
    )

    response = await client.post(
        "/teams/open-team/invitations/",
        json={"email": "anyone@anywhere.io", "role": "member"},
        headers=superuser_inertia_headers,
    )
    assert response.status_code == 303

    async with sessionmaker() as session:
        invitation_service = TeamInvitationService(session=session)
        invites = await invitation_service.list()
        assert any(inv.email == "anyone@anywhere.io" for inv in invites)


async def test_accept_invitation_blocked_by_domain_mismatch(
    client: "AsyncClient",
    sessionmaker: "async_sessionmaker[AsyncSession]",
    user_inertia_headers: dict[str, str],
) -> None:
    """Defense in depth: if a stale invite slips through, accept rejects it.

    Bypasses the controller domain check by creating the invitation through the
    service layer directly.
    """
    await _create_team_with_domain(
        sessionmaker, name="Locked Team", slug="locked-team", domain="certora.com"
    )

    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        user_service = UserService(session=session)
        invitation_service = TeamInvitationService(session=session)
        team = await team_service.get_one(slug="locked-team")
        inviter = await user_service.get_one(email="superuser@example.com")
        _, token = await invitation_service.create_invitation(
            team=team,
            email="user@example.com",
            role=TeamRoles.MEMBER,
            invited_by=inviter,
        )
        await invitation_service.repository.session.commit()

    response = await client.post(
        f"/invitations/{token}/accept",
        headers=user_inertia_headers,
    )
    assert response.status_code == 303

    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        team = await team_service.get_one(slug="locked-team")
        member_user_ids = {m.user_id for m in team.members if isinstance(m, TeamMember)}
        assert team.members  # owner is still present
        assert UUID(USER_ID) not in member_user_ids


# ---------------------------------------------------------------------------
# Operator team
# ---------------------------------------------------------------------------


async def test_operator_team_settings_defaults() -> None:
    """The operator settings default to the Certora platform team."""
    settings = OperatorTeamSettings()
    assert settings.name == "Certora"
    assert settings.domain == "certora.com"


async def test_operator_team_settings_cached() -> None:
    """The cached getter returns the same instance across calls."""
    assert get_operator_team_settings() is get_operator_team_settings()


async def test_ensure_operator_team_creates_when_missing(
    sessionmaker: "async_sessionmaker[AsyncSession]",
) -> None:
    """A new operator team is created when no operator team exists yet."""
    async with sessionmaker() as session:
        # Wipe any operator team a previous fixture/lifespan may have left behind.
        await session.execute(delete(Team).where(Team.is_operator.is_(True)))
        await session.commit()

    async with sessionmaker() as session:
        service = TeamService(session=session)
        team = await service.ensure_operator_team(name="Acme", domain="acme.io")

    assert team is not None
    assert team.is_operator is True
    assert team.name == "Acme"
    assert team.domain == "acme.io"

    async with sessionmaker() as session:
        service = TeamService(session=session)
        all_operators = await service.list(Team.is_operator.is_(True))
        assert len(all_operators) == 1


async def test_ensure_operator_team_idempotent(
    sessionmaker: "async_sessionmaker[AsyncSession]",
) -> None:
    """Re-running the bootstrap does not produce duplicate operator teams."""
    async with sessionmaker() as session:
        await session.execute(delete(Team).where(Team.is_operator.is_(True)))
        await session.commit()

    async with sessionmaker() as session:
        service = TeamService(session=session)
        first = await service.ensure_operator_team(name="Acme", domain="acme.io")

    async with sessionmaker() as session:
        service = TeamService(session=session)
        second = await service.ensure_operator_team(name="Different", domain="other.io")

    assert first is not None
    assert second is not None
    assert first.id == second.id
    # The existing record's name/domain are preserved on the second call.
    assert second.name == "Acme"
    assert second.domain == "acme.io"

    async with sessionmaker() as session:
        service = TeamService(session=session)
        all_operators = await service.list(Team.is_operator.is_(True))
        assert len(all_operators) == 1


async def test_ensure_operator_team_skips_when_no_superuser(
    sessionmaker: "async_sessionmaker[AsyncSession]",
) -> None:
    """If no superuser exists, the team is not created (will retry on next start)."""
    async with sessionmaker() as session:
        await session.execute(delete(Team).where(Team.is_operator.is_(True)))
        await session.execute(
            update(User).where(User.is_superuser.is_(True)).values(is_superuser=False)
        )
        await session.commit()

    async with sessionmaker() as session:
        service = TeamService(session=session)
        team = await service.ensure_operator_team(name="Acme", domain="acme.io")

    assert team is None

    async with sessionmaker() as session:
        service = TeamService(session=session)
        all_operators = await service.list(Team.is_operator.is_(True))
        assert all_operators == []


async def test_ensure_operator_team_uses_first_superuser_as_owner(
    sessionmaker: "async_sessionmaker[AsyncSession]",
) -> None:
    """The first superuser by created_at becomes the owner/admin."""
    async with sessionmaker() as session:
        await session.execute(delete(Team).where(Team.is_operator.is_(True)))
        await session.commit()

    async with sessionmaker() as session:
        service = TeamService(session=session)
        team = await service.ensure_operator_team(name="Acme", domain="acme.io")
    assert team is not None

    async with sessionmaker() as session:
        result = await session.execute(
            select(User)
            .where(User.is_superuser.is_(True))
            .order_by(User.created_at)
            .limit(1)
        )
        first_superuser = result.scalar_one()

        service = TeamService(session=session)
        refreshed = await service.get(team.id)
        owners = [m for m in refreshed.members if m.is_owner]
        assert len(owners) == 1
        assert owners[0].user_id == first_superuser.id


async def test_operator_team_provisioned_on_app_startup(
    client: "AsyncClient",  # noqa: ARG001  -- triggers lifespan/on_startup
    sessionmaker: "async_sessionmaker[AsyncSession]",
) -> None:
    """The on_startup hook materializes the operator team for a fresh deployment."""
    settings = get_operator_team_settings()
    async with sessionmaker() as session:
        service = TeamService(session=session)
        operators = await service.list(Team.is_operator.is_(True))
        assert len(operators) == 1
        assert operators[0].name == settings.name
        assert operators[0].domain == settings.domain


# ---------------------------------------------------------------------------
# Operator guards
# ---------------------------------------------------------------------------


class _FakeConnection:
    """Minimal stand-in for ``ASGIConnection`` exposing the bits guards use."""

    def __init__(self, user: User) -> None:
        self.user = user
        self.path_params: dict[str, str] = {}


async def _ensure_operator_with(
    sessionmaker: "async_sessionmaker[AsyncSession]",
    *,
    member_email: str | None = None,
    role: TeamRoles = TeamRoles.MEMBER,
    is_owner: bool = False,
) -> None:
    """Provision an operator team and optionally add a non-owner member.

    The first superuser is the owner/admin (set by ``ensure_operator_team``).
    When ``member_email`` is given, that user is attached with the requested
    role / ownership flag.
    """
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        await team_service.ensure_operator_team(name="Op", domain="example.com")

    if member_email is None:
        return

    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        user_service = UserService(session=session)
        member_service = TeamMemberService(session=session)
        operator_team = await team_service.get_one(is_operator=True)
        member_user = await user_service.get_one(email=member_email)
        await member_service.create(
            {
                "team_id": operator_team.id,
                "user_id": member_user.id,
                "role": role,
                "is_owner": is_owner,
            }
        )
        await member_service.repository.session.commit()


async def _load_user(
    sessionmaker: "async_sessionmaker[AsyncSession]", email: str
) -> User:
    """Fetch a user with ``teams`` and ``roles`` eagerly loaded."""
    async with sessionmaker() as session:
        user_service = UserService(session=session)
        return await user_service.get_one(email=email)


# Operator guards (requires_operator_member / _admin / _editor) are async and
# query the DB via the request-scoped session, so they're tested end-to-end
# against the HTTP layer in tests/integration/test_manual_metrics.py rather
# than through direct calls here.
