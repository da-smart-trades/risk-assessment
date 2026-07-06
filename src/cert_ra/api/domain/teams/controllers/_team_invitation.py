# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Team invitation controller."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from advanced_alchemy.extensions.litestar.providers import create_service_provider
from litestar import Controller, Request, delete, get, post
from litestar.di import Provide
from litestar.exceptions import PermissionDeniedException
from litestar.params import Parameter
from litestar_vite.inertia import InertiaRedirect, flash
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.teams.guards import requires_team_admin
from cert_ra.api.domain.teams.schemas import (
    CurrentTeam,
    TeamDetail,
    TeamInvitationCreate,
    TeamInvitationItem,
    TeamInvitationsPage,
    TeamPermissions,
)
from cert_ra.api.domain.teams.services import (
    TeamInvitationService,
    TeamMemberService,
    TeamService,
)
from cert_ra.db.models import (
    Team as TeamModel,
    TeamInvitation as TeamInvitationModel,
    TeamMember,
    TeamRoles,
    User as UserModel,
)
from cert_ra.db.models.team_invitation import InvitationKind

if TYPE_CHECKING:
    from uuid import UUID

__all__ = ("TeamInvitationController",)


async def _provision_invitee(
    *,
    users_service: UserService,
    team_members_service: TeamMemberService,
    team: TeamModel,
    normalized_email: str,
    role: TeamRoles,
    inviter: UserModel,
) -> tuple[UserModel, InvitationKind]:
    """Find or pre-provision the invitee, returning ``(user, invitation kind)``.

    A brand-new or never-activated invitee is given a ``User`` row and team
    membership so the first-time-activation flow has an account to activate
    and the set-password card renders (the activation page gates that card on
    a non-NULL invitation ``user_id``). An already-activated user instead gets
    a ``CROSS_TEAM_JOIN`` invite — they accept while signed in, and the
    membership is created on accept.

    Mirrors the add-member and admin-invite provisioning paths. Without it the
    invitation lands with ``user_id`` NULL and the invitee is never prompted
    to set a password.

    Args:
        users_service: User service (its session is the shared request session).
        team_members_service: Team-member service for membership creation.
        team: The target team.
        normalized_email: The invitee email, already lower-cased.
        role: Role the invitee will hold.
        inviter: The admin issuing the invite (recorded as ``invited_by``).

    Returns:
        The invitee ``User`` and the ``InvitationKind`` to stamp on the invite.
    """
    db_session = users_service.repository.session
    invitee = await db_session.scalar(
        select(UserModel).where(func.lower(UserModel.email) == normalized_email)
    )

    if invitee is not None:
        # hashed_password is a deferred column — query the activation columns
        # directly rather than touching the loaded ORM attribute.
        state = (
            await db_session.execute(
                select(UserModel.activated_at, UserModel.hashed_password).where(
                    UserModel.id == invitee.id
                )
            )
        ).one()
        if state.activated_at is not None or state.hashed_password is not None:
            # Already activated: a cross-team join, accepted while signed in.
            return invitee, InvitationKind.CROSS_TEAM_JOIN
    else:
        invitee = await users_service.create(
            {
                "email": normalized_email,
                "is_verified": False,
                "is_active": True,
                "invited_by_user_id": inviter.id,
            }
        )

    # New or never-activated: pre-create the membership so the set-password
    # activation flow (which assumes membership already exists) lands the user
    # on their team.
    await team_members_service.create(
        {"team_id": team.id, "user_id": invitee.id, "role": role}
    )
    return invitee, InvitationKind.FIRST_TIME_ACTIVATION


class TeamInvitationController(Controller):
    """Team Invitations."""

    tags = ["Teams"]  # noqa: RUF012
    guards = [requires_active_user]  # noqa: RUF012
    dependencies = {  # noqa: RUF012
        "teams_service": create_service_provider(
            TeamService,
            load=[
                selectinload(TeamModel.tags),
                selectinload(TeamModel.members).options(
                    joinedload(TeamMember.user, innerjoin=True)
                ),
            ],
        ),
        "team_invitations_service": create_service_provider(
            TeamInvitationService,
            load=[
                joinedload(TeamInvitationModel.team),
                joinedload(TeamInvitationModel.invited_by),
            ],
        ),
        "team_members_service": create_service_provider(TeamMemberService),
        "users_service": Provide(provide_users_service),
    }
    signature_namespace = {  # noqa: RUF012
        "TeamService": TeamService,
        "TeamInvitationService": TeamInvitationService,
        "TeamMemberService": TeamMemberService,
        "TeamInvitationCreate": TeamInvitationCreate,
        "UserService": UserService,
    }

    @get(
        component="team/invitations",
        name="teams.invitations",
        operation_id="GetTeamInvitations",
        guards=[requires_team_admin],
        path="/teams/{team_slug:str}/invitations/",
    )
    async def get_team_invitations(
        self,
        request: Request,
        teams_service: TeamService,
        team_invitations_service: TeamInvitationService,
        users_service: UserService,
        current_user: UserModel,
        team_slug: Annotated[
            str, Parameter(title="Team Slug", description="The team slug.")
        ],
    ) -> TeamInvitationsPage:
        """Get pending invitations for a team.

        Returns:
            Team details and list of pending invitations.
        """
        db_obj = await teams_service.get_one(slug=team_slug)
        request.session.update(
            {"currentTeam": CurrentTeam(team_id=db_obj.id, team_name=db_obj.name)}
        )

        invitations = await team_invitations_service.get_pending_for_team(db_obj.id)
        accepted = await team_invitations_service.get_accepted_for_team(db_obj.id)

        membership = next(
            (m for m in db_obj.members if m.user_id == current_user.id), None
        )
        is_owner = bool(membership and membership.is_owner)
        is_admin = is_owner or bool(membership and membership.role == TeamRoles.ADMIN)

        invitee_flags: dict[str, bool] = {}
        if invitations:
            emails = {inv.email for inv in invitations}
            result = await users_service.repository.session.execute(
                select(UserModel.email).where(UserModel.email.in_(emails)),
            )
            existing_emails = {row[0] for row in result}
            invitee_flags = {email: email in existing_emails for email in emails}

        return TeamInvitationsPage(
            team=TeamDetail(
                id=db_obj.id,
                name=db_obj.name,
                slug=db_obj.slug,
                description=db_obj.description,
                domain=db_obj.domain,
            ),
            invitations=[
                TeamInvitationItem(
                    id=inv.id,
                    email=inv.email,
                    role=str(inv.role),
                    invited_by_email=inv.invited_by_email,
                    created_at=inv.created_at,
                    expires_at=inv.expires_at,
                    is_expired=inv.is_expired,
                    invitee_exists=invitee_flags.get(inv.email, False),
                )
                for inv in invitations
            ],
            accepted_invitations=[
                TeamInvitationItem(
                    id=inv.id,
                    email=inv.email,
                    role=str(inv.role),
                    invited_by_email=inv.invited_by_email,
                    created_at=inv.created_at,
                    expires_at=inv.expires_at,
                    is_expired=inv.is_expired,
                    invitee_exists=True,
                    accepted_at=inv.accepted_at,
                )
                for inv in accepted
            ],
            permissions=TeamPermissions(
                can_add_team_members=is_admin,
                can_delete_team=is_owner,
                can_remove_team_members=is_admin,
                can_update_team=is_admin,
            ),
        )

    @post(
        name="teams.invite",
        operation_id="CreateTeamInvitation",
        guards=[requires_team_admin],
        path="/teams/{team_slug:str}/invitations/",
    )
    async def create_invitation(
        self,
        request: Request,
        teams_service: TeamService,
        team_invitations_service: TeamInvitationService,
        team_members_service: TeamMemberService,
        users_service: UserService,
        current_user: UserModel,
        data: TeamInvitationCreate,
        team_slug: Annotated[
            str, Parameter(title="Team Slug", description="The team slug.")
        ],
    ) -> InertiaRedirect:
        """Create a new team invitation.

        Pre-provisions a User row (and team membership) for a brand-new or
        never-activated invitee, mirroring the add-member and admin-invite
        paths, so the invitation carries a ``user_id`` and the recipient is
        shown the set-password / SSO activation card. Without this the invite
        lands with ``user_id`` NULL and the activation step is silently
        skipped (the recipient dead-ends on the generic login card).

        Returns:
            Redirect to invitations page.
        """
        team_obj = await teams_service.get_one(slug=team_slug)

        if team_obj.domain:
            invitee_domain = data.email.rsplit("@", 1)[-1].lower()
            if invitee_domain != team_obj.domain:
                flash(
                    request,
                    f"This team only accepts members with @{team_obj.domain} email addresses.",
                    category="error",
                )
                return InertiaRedirect(
                    request,
                    request.url_for("teams.invitations", team_slug=team_slug),
                )

        is_member = any(m.user.email == data.email for m in team_obj.members)
        if is_member:
            flash(
                request, "This user is already a member of the team.", category="error"
            )
            return InertiaRedirect(
                request, request.url_for("teams.invitations", team_slug=team_slug)
            )

        has_pending = await team_invitations_service.has_pending_invitation(
            team_obj.id, data.email
        )
        if has_pending:
            flash(
                request,
                "An invitation has already been sent to this email.",
                category="error",
            )
            return InertiaRedirect(
                request, request.url_for("teams.invitations", team_slug=team_slug)
            )

        normalized_email = data.email.strip().lower()
        invitee, kind = await _provision_invitee(
            users_service=users_service,
            team_members_service=team_members_service,
            team=team_obj,
            normalized_email=normalized_email,
            role=data.role,
            inviter=current_user,
        )

        _, token = await team_invitations_service.create_invitation(
            team=team_obj,
            email=normalized_email,
            role=data.role,
            invited_by=current_user,
            kind=kind,
            user_id=invitee.id,
            # force_provider only drives the FIRST_TIME_ACTIVATION activation
            # page (redirect straight into the team's IdP, skipping password).
            # A cross-team join is accepted by an already-signed-in user.
            force_provider=(
                team_obj.enforced_provider
                if kind == InvitationKind.FIRST_TIME_ACTIVATION
                else None
            ),
        )

        request.app.emit(
            "team_invitation_created",
            invitee_email=normalized_email,
            inviter_name=current_user.name or current_user.email,
            team_name=team_obj.name,
            token=token,
        )

        flash(
            request,
            f"{normalized_email} was invited to join {team_obj.name}. "
            "They'll get an email to finish setting up their account.",
            category="success",
        )

        redirect_target = request.headers.get("referer") or request.url_for(
            "teams.invitations", team_slug=team_slug
        )
        return InertiaRedirect(request, redirect_target)

    @delete(
        name="teams.invitation.cancel",
        operation_id="CancelTeamInvitation",
        guards=[requires_team_admin],
        path="/teams/{team_slug:str}/invitations/{invitation_id:uuid}",
        status_code=303,
    )
    async def cancel_invitation(
        self,
        request: Request,
        team_invitations_service: TeamInvitationService,
        team_slug: Annotated[
            str, Parameter(title="Team Slug", description="The team slug.")
        ],
        invitation_id: Annotated[
            UUID, Parameter(title="Invitation ID", description="The invitation ID.")
        ],
    ) -> InertiaRedirect:
        """Cancel a pending invitation.

        Raises:
            PermissionDeniedException: If the invitation does not belong to the team.

        Returns:
            Redirect to invitations page.
        """
        invitation = await team_invitations_service.get_one_or_none(id=invitation_id)
        if invitation is None or invitation.team.slug != team_slug:
            msg = "Invitation does not belong to this team."
            raise PermissionDeniedException(detail=msg)
        await team_invitations_service.delete(invitation_id)
        flash(request, "Invitation cancelled.", category="info")
        return InertiaRedirect(
            request, request.url_for("teams.invitations", team_slug=team_slug)
        )
