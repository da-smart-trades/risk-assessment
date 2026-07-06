# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Team member controller."""

from __future__ import annotations

from typing import Annotated

from advanced_alchemy.extensions.litestar.providers import create_service_provider
from litestar import Controller, Request, post
from litestar.di import Provide
from litestar.exceptions import ValidationException
from litestar.params import Parameter
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, noload, selectinload

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.teams.guards import requires_team_admin
from cert_ra.api.domain.teams.schemas import (
    Team,
    TeamMemberModify,
    TeamMemberRoleChange,
)
from cert_ra.api.domain.teams.services import (
    TeamInvitationService,
    TeamMemberService,
    TeamService,
)
from cert_ra.api.lib.schema import Message
from cert_ra.db.models import (
    Team as TeamModel,
    TeamInvitation as TeamInvitationModel,
    TeamMember,
    TeamRoles,
    User as UserModel,
)
from cert_ra.db.models.team_invitation import InvitationKind

__all__ = ("TeamMemberController",)

# Exception messages
_MSG_USER_NOT_FOUND = "User not found"
_MSG_USER_ALREADY_MEMBER = "User is already a member of the team"
_MSG_USER_NOT_MEMBER = "User is not a member of this team"
_MSG_INVITATION_PENDING = "An invitation has already been sent to this email"
_MSG_CANNOT_REMOVE_OWNER = (
    "The team owner can't be removed. Transfer ownership to another member first."
)


class TeamMemberController(Controller):
    """Team Members."""

    tags = ["Team Members"]  # noqa: RUF012
    guards = [requires_active_user, requires_team_admin]  # noqa: RUF012
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
        "team_members_service": create_service_provider(
            TeamMemberService,
            load=[
                noload("*"),
                joinedload(TeamMember.team, innerjoin=True).options(noload("*")),
                joinedload(TeamMember.user, innerjoin=True).options(noload("*")),
            ],
        ),
        "team_invitations_service": create_service_provider(
            TeamInvitationService,
            load=[TeamInvitationModel.team, TeamInvitationModel.invited_by],
        ),
        "users_service": Provide(provide_users_service),
    }
    signature_namespace = {  # noqa: RUF012
        "TeamService": TeamService,
        "UserService": UserService,
        "TeamMemberService": TeamMemberService,
        "TeamInvitationService": TeamInvitationService,
        "Team": Team,
    }

    @post(
        operation_id="AddMemberToTeam",
        name="teams:add-member",
        path="/api/teams/{team_slug:str}/members/add",
    )
    async def add_member_to_team(
        self,
        request: Request,
        teams_service: TeamService,
        team_members_service: TeamMemberService,
        team_invitations_service: TeamInvitationService,
        users_service: UserService,
        current_user: UserModel,
        data: TeamMemberModify,
        team_slug: Annotated[
            str, Parameter(title="Team Slug", description="The team slug.")
        ],
    ) -> Message:
        """Add a member to a team, or send an invitation if the user doesn't exist.

        If a User row already exists for the email (regardless of case),
        they are added directly to the team. If they exist but were never
        activated (admin pre-provisioned but never completed sign-in), a
        FIRST_TIME_ACTIVATION invitation is sent so they can set a
        password or sign in via OIDC.

        If no User row exists, one is pre-provisioned (mirroring the
        admin invite endpoint) so the OIDC resolver can match the invitee
        on first sign-in, and a FIRST_TIME_ACTIVATION invitation is sent.
        Honors the team's ``enforced_provider`` as ``force_provider`` so
        SSO-locked teams skip the password option.

        Returns:
            Message describing whether the user was added or invited.

        Raises:
            ValidationException: If the user is already a member or has a pending invitation.
        """
        team_obj = await teams_service.get_one(slug=team_slug)
        db_session = users_service.repository.session
        normalized_email = data.user_name.strip().lower()
        user_obj = await db_session.scalar(
            select(UserModel).where(func.lower(UserModel.email) == normalized_email)
        )

        if user_obj:
            is_member = any(
                membership.team_id == team_obj.id for membership in user_obj.teams
            )
            if is_member:
                raise ValidationException(_MSG_USER_ALREADY_MEMBER)
            await team_members_service.create(
                {
                    "team_id": team_obj.id,
                    "user_id": user_obj.id,
                    "role": TeamRoles.MEMBER,
                }
            )
            # An account can exist without ever having been activated — e.g.
            # one an operator pre-provisioned for SSO that the user never
            # completed. Adding such a user as a bare member would leave them
            # with no way to sign in (no password, not activated), so send a
            # first-time-activation invite (password or SSO) instead of
            # silently creating an unreachable member. hashed_password is a
            # deferred column — query the activation columns directly.
            state = (
                await db_session.execute(
                    select(UserModel.activated_at, UserModel.hashed_password).where(
                        UserModel.id == user_obj.id
                    )
                )
            ).one()
            is_activated = (
                state.activated_at is not None or state.hashed_password is not None
            )
            if is_activated:
                message = (
                    f"{user_obj.email} is already registered "
                    f"and was added to {team_obj.name}."
                )
            else:
                if not await team_invitations_service.has_pending_invitation(
                    team_obj.id, user_obj.email
                ):
                    _, token = await team_invitations_service.create_invitation(
                        team=team_obj,
                        email=user_obj.email,
                        role=TeamRoles.MEMBER,
                        invited_by=current_user,
                        kind=InvitationKind.FIRST_TIME_ACTIVATION,
                        user_id=user_obj.id,
                        force_provider=team_obj.enforced_provider,
                    )
                    request.app.emit(
                        "team_invitation_created",
                        invitee_email=user_obj.email,
                        inviter_name=current_user.name or current_user.email,
                        team_name=team_obj.name,
                        token=token,
                    )
                message = (
                    f"{user_obj.email} was added to {team_obj.name} and emailed "
                    "a link to finish setting up their account."
                )
        else:
            # No User row yet — pre-provision one so the OIDC resolver
            # can match the invitee on first sign-in (admin-driven
            # provisioning model). Mirrors AdminTeamController.invite_member.
            if await team_invitations_service.has_pending_invitation(
                team_obj.id, normalized_email
            ):
                raise ValidationException(_MSG_INVITATION_PENDING)
            new_user = await users_service.create(
                {
                    "email": normalized_email,
                    "is_verified": False,
                    "is_active": True,
                    "invited_by_user_id": current_user.id,
                }
            )
            await team_members_service.create(
                {
                    "team_id": team_obj.id,
                    "user_id": new_user.id,
                    "role": TeamRoles.MEMBER,
                }
            )
            _, token = await team_invitations_service.create_invitation(
                team=team_obj,
                email=normalized_email,
                role=TeamRoles.MEMBER,
                invited_by=current_user,
                kind=InvitationKind.FIRST_TIME_ACTIVATION,
                user_id=new_user.id,
                force_provider=team_obj.enforced_provider,
            )
            request.app.emit(
                "team_invitation_created",
                invitee_email=normalized_email,
                inviter_name=current_user.name or current_user.email,
                team_name=team_obj.name,
                token=token,
            )
            message = (
                f"{normalized_email} was invited to join {team_obj.name}. "
                "They'll receive an email to finish setting up their account."
            )

        return Message(message=message)

    @post(
        operation_id="RemoveMemberFromTeam",
        name="teams:remove-member",
        summary="Remove Team Member",
        description="Removes a member from a team",
        path="/api/teams/{team_slug:str}/members/remove",
        status_code=200,
    )
    async def remove_member_from_team(
        self,
        teams_service: TeamService,
        team_members_service: TeamMemberService,
        users_service: UserService,
        data: TeamMemberModify,
        team_slug: Annotated[
            str, Parameter(title="Team Slug", description="The team slug.")
        ],
    ) -> Team:
        """Remove a member from a team.

        Returns:
            Updated team data without the removed member.

        Raises:
            ValidationException: If the user is not found or not a member of this team.
        """
        team_obj = await teams_service.get_one(slug=team_slug)
        db_session = users_service.repository.session
        normalized_email = data.user_name.strip().lower()
        user_obj = await db_session.scalar(
            select(UserModel).where(func.lower(UserModel.email) == normalized_email)
        )
        if not user_obj:
            raise ValidationException(_MSG_USER_NOT_FOUND)
        membership = next(
            (
                membership
                for membership in user_obj.teams
                if membership.team_id == team_obj.id
            ),
            None,
        )
        if not membership:
            raise ValidationException(_MSG_USER_NOT_MEMBER)
        if membership.is_owner:
            raise ValidationException(_MSG_CANNOT_REMOVE_OWNER)
        _ = await team_members_service.delete(membership.id)
        await users_service.delete_if_orphaned(user_obj.id)
        team_obj = await teams_service.get_one(slug=team_slug)
        return teams_service.to_schema(schema_type=Team, data=team_obj)

    @post(
        operation_id="ChangeMemberRole",
        name="teams:change-member-role",
        summary="Change Team Member Role",
        description="Update the role of an existing team member.",
        path="/api/teams/{team_slug:str}/members/role",
        status_code=200,
    )
    async def change_member_role(
        self,
        teams_service: TeamService,
        team_members_service: TeamMemberService,
        users_service: UserService,
        data: TeamMemberRoleChange,
        team_slug: Annotated[
            str, Parameter(title="Team Slug", description="The team slug.")
        ],
    ) -> Team:
        """Change a member's role on a team.

        The team owner's role cannot be changed via this endpoint — owners
        are tracked separately on the membership row.

        Returns:
            Updated team data.

        Raises:
            ValidationException: If the user is not a member, if the role is
                not a valid ``TeamRoles`` value, or if the target is the
                team owner.
        """
        try:
            new_role = TeamRoles(data.role.lower())
        except ValueError as exc:
            valid_roles = ", ".join(r.value for r in TeamRoles)
            msg = f"Invalid role '{data.role}'. Expected one of: {valid_roles}."
            raise ValidationException(msg) from exc
        team_obj = await teams_service.get_one(slug=team_slug)
        db_session = users_service.repository.session
        normalized_email = data.user_name.strip().lower()
        user_obj = await db_session.scalar(
            select(UserModel).where(func.lower(UserModel.email) == normalized_email)
        )
        if not user_obj:
            raise ValidationException(_MSG_USER_NOT_FOUND)
        membership = next(
            (
                membership
                for membership in user_obj.teams
                if membership.team_id == team_obj.id
            ),
            None,
        )
        if not membership:
            raise ValidationException(_MSG_USER_NOT_MEMBER)
        if membership.is_owner:
            msg = "Team owners cannot have their role changed via this endpoint."
            raise ValidationException(msg)
        await team_members_service.update(
            item_id=membership.id, data={"role": new_role}
        )
        team_obj = await teams_service.get_one(slug=team_slug)
        return teams_service.to_schema(schema_type=Team, data=team_obj)
