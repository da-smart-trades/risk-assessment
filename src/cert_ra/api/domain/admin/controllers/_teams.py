# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Admin teams controller."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from advanced_alchemy.extensions.litestar.providers import create_service_dependencies
from litestar import Controller, Request, delete, get, patch, post
from litestar.di import Provide
from litestar.params import Dependency, Parameter
from litestar_vite.inertia import InertiaRedirect, flash
from sqlalchemy.orm import joinedload, selectinload

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.admin.dependencies import provide_audit_service
from cert_ra.api.domain.admin.schemas import (
    AdminTeamDetail,
    AdminTeamDetailPage,
    AdminTeamListItem,
    AdminTeamListPage,
    AdminTeamMemberAdd,
    AdminTeamMemberInvite,
    AdminTeamUpdate,
    TeamMemberInfo,
)
from cert_ra.api.domain.teams.dependencies import (
    provide_team_invitations_service,
    provide_team_members_service,
)
from cert_ra.api.domain.teams.guards import (
    requires_operator_member,
    requires_operator_tenant_admin,
)
from cert_ra.api.domain.teams.services import (
    TeamInvitationService,
    TeamMemberService,
    TeamService,
)
from cert_ra.api.lib.operator_audit import (
    OperatorAction,
    emit_operator_audit_fanout,
    record_operator_action,
)
from cert_ra.db.models import (
    AuditAction,
    Team as TeamModel,
    TeamMember,
    User as UserModel,
)
from cert_ra.db.models.team_invitation import InvitationKind

if TYPE_CHECKING:
    from advanced_alchemy.filters import FilterTypes

    from cert_ra.api.domain.admin.services import AuditLogService

__all__ = ("AdminTeamController",)


class AdminTeamController(Controller):
    """Admin team management."""

    tags = ["Admin - Teams"]  # noqa: RUF012
    path = "/admin/teams"
    # Reads are open to any operator-team member (incl. operator_support);
    # write routes add ``requires_operator_tenant_admin`` (PR-8, Control 2).
    guards = [requires_operator_member]  # noqa: RUF012
    dependencies = create_service_dependencies(
        TeamService,
        key="teams_service",
        load=[
            selectinload(TeamModel.members).options(
                joinedload(TeamMember.user, innerjoin=True)
            )
        ],
        filters={
            "id_filter": UUID,
            "search": "name",
            "pagination_type": "limit_offset",
            "pagination_size": 25,
            "created_at": True,
            "updated_at": True,
            "sort_field": "created_at",
            "sort_order": "desc",
        },
    ) | {
        "audit_service": Provide(provide_audit_service),
        "team_members_service": Provide(provide_team_members_service),
        "team_invitations_service": Provide(provide_team_invitations_service),
        "users_service": Provide(provide_users_service),
    }
    signature_namespace = {  # noqa: RUF012
        "TeamInvitationService": TeamInvitationService,
        "UserService": UserService,
        "AdminTeamMemberInvite": AdminTeamMemberInvite,
        "InvitationKind": InvitationKind,
    }

    @get(
        component="admin/teams/list",
        name="admin.teams.list",
        operation_id="AdminListTeams",
        path="/",
    )
    async def list_teams(
        self,
        teams_service: TeamService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> AdminTeamListPage:
        """List all teams for admin management.

        Returns:
            Paginated list of teams.
        """
        results, total = await teams_service.list_and_count(*filters)

        teams = [
            AdminTeamListItem(
                id=t.id,
                name=t.name,
                slug=t.slug,
                description=t.description,
                is_active=t.is_active,
                member_count=len(t.members),
                owner_email=next((m.user.email for m in t.members if m.is_owner), None),
                created_at=t.created_at,
            )
            for t in results
        ]

        return AdminTeamListPage(teams=teams, total=total)

    @get(
        component="admin/teams/detail",
        name="admin.teams.detail",
        operation_id="AdminGetTeam",
        path="/{team_id:uuid}/",
    )
    async def get_team(
        self,
        teams_service: TeamService,
        team_id: Annotated[
            UUID, Parameter(title="Team ID", description="The team ID.")
        ],
    ) -> AdminTeamDetailPage:
        """Get team details for admin.

        Returns:
            Team details with member list.
        """
        team = await teams_service.get(team_id)

        return AdminTeamDetailPage(
            team=AdminTeamDetail(
                id=team.id,
                name=team.name,
                slug=team.slug,
                description=team.description,
                is_active=team.is_active,
                is_operator=team.is_operator,
                members=[
                    TeamMemberInfo(
                        id=m.id,
                        user_id=m.user_id,
                        email=m.user.email,
                        name=m.user.name,
                        role=m.role,
                        is_owner=m.is_owner,
                        avatar_url=m.user.avatar_url,
                    )
                    for m in team.members
                ],
                created_at=team.created_at,
                updated_at=team.updated_at,
            ),
        )

    @patch(
        name="admin.teams.update",
        operation_id="AdminUpdateTeam",
        path="/{team_id:uuid}/",
        guards=[requires_operator_tenant_admin],
    )
    async def update_team(
        self,
        request: Request,
        teams_service: TeamService,
        audit_service: AuditLogService,
        current_user: UserModel,
        team_id: Annotated[
            UUID, Parameter(title="Team ID", description="The team ID.")
        ],
        data: AdminTeamUpdate,
    ) -> InertiaRedirect:
        """Update a team.

        Returns:
            Redirect to team detail page.
        """
        db_obj = await teams_service.update(item_id=team_id, data=data.to_dict())
        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.TEAM_UPDATED,
            target_type="team",
            target_id=db_obj.id,
            target_label=db_obj.name,
            details=data.to_dict(),
            ip_address=request.client.host if request.client else None,
        )
        flash(request, f"Updated team {db_obj.name}", category="success")
        return InertiaRedirect(
            request, request.url_for("admin.teams.detail", team_id=db_obj.id)
        )

    @post(
        name="admin.teams.members.add",
        operation_id="AdminAddTeamMember",
        path="/{team_id:uuid}/members/",
        guards=[requires_operator_tenant_admin],
    )
    async def add_member(
        self,
        request: Request,
        teams_service: TeamService,
        team_members_service: TeamMemberService,
        audit_service: AuditLogService,
        current_user: UserModel,
        team_id: Annotated[
            UUID, Parameter(title="Team ID", description="The team ID.")
        ],
        data: AdminTeamMemberAdd,
    ) -> InertiaRedirect:
        """Add a member to a team.

        Returns:
            Redirect to team detail page.
        """
        team = await teams_service.get(team_id)
        await team_members_service.create(
            {
                "team_id": team_id,
                "user_id": data.user_id,
                "role": data.role,
                "is_owner": False,
            }
        )

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.TEAM_MEMBER_ADDED,
            target_type="team",
            target_id=team_id,
            target_label=team.name,
            details={"user_id": str(data.user_id), "role": str(data.role)},
            ip_address=request.client.host if request.client else None,
        )
        flash(request, f"Added member to team {team.name}", category="success")
        return InertiaRedirect(
            request, request.url_for("admin.teams.detail", team_id=team_id)
        )

    @post(
        name="admin.teams.members.make_owner",
        operation_id="AdminMakeTeamOwner",
        path="/{team_id:uuid}/members/{member_id:uuid}/make-owner/",
        status_code=303,
        guards=[requires_operator_tenant_admin],
    )
    async def make_owner(
        self,
        request: Request,
        teams_service: TeamService,
        team_members_service: TeamMemberService,
        audit_service: AuditLogService,
        current_user: UserModel,
        team_id: Annotated[
            UUID, Parameter(title="Team ID", description="The team ID.")
        ],
        member_id: Annotated[
            UUID, Parameter(title="Member ID", description="The team member ID.")
        ],
    ) -> InertiaRedirect:
        """Transfer team ownership to an existing member.

        Makes the member the team's sole owner (demoting the previous
        owner and bumping the new owner to ADMIN). Lets the operator hand
        a freshly provisioned org to its real customer owner.

        Returns:
            Redirect to the team detail page.
        """
        team = await teams_service.get(team_id)
        member = await team_members_service.transfer_ownership(
            team_id=team_id, new_owner_member_id=member_id
        )
        await team_members_service.repository.session.commit()

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.TEAM_OWNERSHIP_TRANSFERRED,
            target_type="team",
            target_id=team_id,
            target_label=team.name,
            details={"new_owner_user_id": str(member.user_id)},
            ip_address=request.client.host if request.client else None,
        )
        flash(
            request,
            f"{member.email} is now the owner of {team.name}.",
            category="success",
        )
        return InertiaRedirect(
            request, request.url_for("admin.teams.detail", team_id=team_id)
        )

    @post(
        name="admin.teams.members.invite",
        operation_id="AdminInviteTeamMember",
        path="/{team_id:uuid}/members/invite/",
        guards=[requires_operator_tenant_admin],
    )
    async def invite_member(
        self,
        request: Request,
        teams_service: TeamService,
        team_invitations_service: TeamInvitationService,
        team_members_service: TeamMemberService,
        users_service: UserService,
        audit_service: AuditLogService,
        current_user: UserModel,
        team_id: Annotated[
            UUID, Parameter(title="Team ID", description="The team ID.")
        ],
        data: AdminTeamMemberInvite,
    ) -> InertiaRedirect | dict[str, list[str]]:
        """Invite a new member via the OIDC SSO admin-provisioning flow.

        Pre-creates a User row (``activated_at=NULL``,
        ``hashed_password=NULL``, ``is_verified=False``) so the invitee
        can complete sign-in via the OIDC resolver without auto-creating.
        Creates a TeamMember row binding them to the team at the
        requested role. Mints a TeamInvitation with
        ``kind=FIRST_TIME_ACTIVATION`` and HMAC-SHA-256 token hashing.

        Out-of-domain check: validates the email's domain against
        ``team.allowed_email_domains``. If outside and
        ``out_of_domain_override=false``, returns the 409-style
        out-of-domain dict so the UI can prompt for confirmation.

        Returns:
            On success: redirect to the team admin detail page.
            On out-of-domain without override: dict with
            ``out_of_domain_required`` so the UI prompts for
            confirmation.
        """
        team = await teams_service.get(team_id)
        normalized_email = data.email.strip().lower()

        # Out-of-domain check (soft enforcement). The override flag is
        # surfaced via the 409 response so the UI can prompt and
        # re-submit with override=true.
        allowed = [d.lower() for d in (team.allowed_email_domains or [])]
        if allowed:
            invitee_domain = normalized_email.rsplit("@", 1)[-1]
            if invitee_domain not in allowed and not data.out_of_domain_override:
                return {
                    "out_of_domain_required": [
                        f"{normalized_email} is outside this team's allowed "
                        f"domains ({', '.join(allowed)}). Re-submit with "
                        "out_of_domain_override=true to confirm."
                    ]
                }

        # If a user with this email already exists, fast-fail rather
        # than silently reuse the row. PR-2b-iii will add the proper
        # "already a member" + "cross-team add" branches; for now we
        # surface the error to the admin.
        existing_user = await users_service.get_one_or_none(email=normalized_email)
        if existing_user is not None:
            return {
                "email": [
                    f"A user already exists for {normalized_email}. "
                    "Cross-team add for existing users lands in a "
                    "later PR."
                ]
            }

        # Pre-provision the User row. activated_at stays NULL until the
        # invitation is consumed via OIDC sign-in.
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
                "team_id": team_id,
                "user_id": new_user.id,
                "role": data.role,
                "is_owner": False,
            }
        )
        invitation, raw_token = await team_invitations_service.create_invitation(
            team=team,
            email=normalized_email,
            role=data.role,
            invited_by=current_user,
            kind=InvitationKind.FIRST_TIME_ACTIVATION,
            user_id=new_user.id,
            force_provider=data.force_provider,
            out_of_domain_override=data.out_of_domain_override,
        )

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.TEAM_MEMBER_ADDED,
            target_type="team",
            target_id=team_id,
            target_label=team.name,
            details={
                "invited_email": normalized_email,
                "role": str(data.role),
                "force_provider": data.force_provider,
                "out_of_domain_override": data.out_of_domain_override,
                "invitation_id": str(invitation.id),
                "kind": str(InvitationKind.FIRST_TIME_ACTIVATION),
            },
            ip_address=request.client.host if request.client else None,
        )

        request.app.emit(
            "team_invitation_created",
            invitee_email=normalized_email,
            inviter_name=current_user.name or current_user.email,
            team_name=team.name,
            token=raw_token,
        )
        if data.out_of_domain_override:
            request.app.emit(
                "out_of_domain_provision_alert",
                team_id=team_id,
                team_name=team.name,
                invitee_email=normalized_email,
                inviter_name=current_user.name or current_user.email,
                allowed_domains=list(allowed),
            )

        flash(
            request,
            f"Invited {normalized_email} to team {team.name}.",
            category="success",
        )
        return InertiaRedirect(
            request, request.url_for("admin.teams.detail", team_id=team_id)
        )

    @post(
        name="admin.teams.members.force_unlock",
        operation_id="AdminForceUnlockTeamMember",
        path="/{team_id:uuid}/members/{member_id:uuid}/force-unlock/",
        status_code=303,
        guards=[requires_operator_tenant_admin],
    )
    async def force_unlock_member(
        self,
        request: Request,
        teams_service: TeamService,
        team_members_service: TeamMemberService,
        audit_service: AuditLogService,
        current_user: UserModel,
        team_id: Annotated[
            UUID, Parameter(title="Team ID", description="The team ID.")
        ],
        member_id: Annotated[
            UUID, Parameter(title="Member ID", description="The team member ID.")
        ],
    ) -> InertiaRedirect:
        """Force-unlock a team member (admin action — design Fix 3).

        Clears every ``UserLockout`` row for the user, resets the
        unlock-email throttle, and notifies the user. The notification
        names the acting team + role but NOT the admin's identity
        (design #16 — never reveal admin identity to other users).
        """
        from cert_ra.api.lib.auth_lockout import force_unlock_user

        team = await teams_service.get(team_id)
        member = await team_members_service.get(member_id)

        db_session = team_members_service.repository.session
        deleted_count = await force_unlock_user(db_session, user_id=member.user_id)
        await record_operator_action(
            db_session,
            request=request,
            actor=current_user,
            action=OperatorAction.FORCE_UNLOCK,
            target_team_id=team.id,
            target_user_id=member.user_id,
            payload={"team_name": team.name, "rows_cleared": deleted_count},
        )
        await db_session.commit()

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.USER_UNLOCKED,
            target_type="team",
            target_id=team_id,
            target_label=team.name,
            details={
                "user_id": str(member.user_id),
                "force_unlock_rows_cleared": deleted_count,
            },
            ip_address=request.client.host if request.client else None,
        )
        emit_operator_audit_fanout(
            request,
            action=OperatorAction.FORCE_UNLOCK,
            actor_email=current_user.email,
            target_team_name=team.name,
            security_contact_email=team.security_contact_email,
        )
        request.app.emit(
            "force_unlock_notification",
            user_id=member.user_id,
            team_name=team.name,
            actor_role="admin",
        )
        flash(request, f"Account unlocked on team {team.name}", category="success")
        return InertiaRedirect(
            request, request.url_for("admin.teams.detail", team_id=team_id)
        )

    @delete(
        name="admin.teams.members.remove",
        operation_id="AdminRemoveTeamMember",
        path="/{team_id:uuid}/members/{member_id:uuid}/",
        status_code=303,
        guards=[requires_operator_tenant_admin],
    )
    async def remove_member(
        self,
        request: Request,
        teams_service: TeamService,
        team_members_service: TeamMemberService,
        users_service: UserService,
        audit_service: AuditLogService,
        current_user: UserModel,
        team_id: Annotated[
            UUID, Parameter(title="Team ID", description="The team ID.")
        ],
        member_id: Annotated[
            UUID, Parameter(title="Member ID", description="The team member ID.")
        ],
    ) -> InertiaRedirect:
        """Remove a member from a team.

        Returns:
            Redirect to team detail page.
        """
        team = await teams_service.get(team_id)
        member = await team_members_service.get(member_id)
        removed_user_id = member.user_id
        removed_user_email = member.email

        await team_members_service.delete(member_id)
        user_deleted = await users_service.delete_if_orphaned(removed_user_id)

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.TEAM_MEMBER_REMOVED,
            target_type="team",
            target_id=team_id,
            target_label=team.name,
            details={"user_id": str(removed_user_id)},
            ip_address=request.client.host if request.client else None,
        )
        if user_deleted:
            await audit_service.log_action(
                actor=current_user,
                action=AuditAction.USER_DELETED,
                target_type="user",
                target_id=removed_user_id,
                target_label=removed_user_email,
                details={"reason": "auto_delete_orphaned_after_last_team_removal"},
                ip_address=request.client.host if request.client else None,
            )
        flash(request, f"Removed member from team {team.name}", category="warning")
        return InertiaRedirect(
            request, request.url_for("admin.teams.detail", team_id=team_id)
        )

    @delete(
        name="admin.teams.delete",
        operation_id="AdminDeleteTeam",
        path="/{team_id:uuid}/",
        status_code=303,
        guards=[requires_operator_tenant_admin],
    )
    async def delete_team(
        self,
        request: Request,
        teams_service: TeamService,
        users_service: UserService,
        audit_service: AuditLogService,
        current_user: UserModel,
        team_id: Annotated[
            UUID, Parameter(title="Team ID", description="The team ID.")
        ],
    ) -> InertiaRedirect:
        """Delete a team.

        Returns:
            Redirect to teams list.
        """
        db_obj = await teams_service.get(team_id)
        name = db_obj.name
        # Snapshot member identities before cascade-delete wipes them. We can't
        # query TeamMember after, so any user whose only team was this one would
        # otherwise be lost to the orphan-cleanup pass.
        candidate_users = [(m.user_id, m.email) for m in db_obj.members]
        await teams_service.delete(team_id)

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.TEAM_DELETED,
            target_type="team",
            target_id=team_id,
            target_label=name,
            ip_address=request.client.host if request.client else None,
        )
        for user_id, email in candidate_users:
            if await users_service.delete_if_orphaned(user_id):
                await audit_service.log_action(
                    actor=current_user,
                    action=AuditAction.USER_DELETED,
                    target_type="user",
                    target_id=user_id,
                    target_label=email,
                    details={"reason": "auto_delete_orphaned_after_team_deletion"},
                    ip_address=request.client.host if request.client else None,
                )
        flash(request, f"Deleted team {name}", category="warning")
        return InertiaRedirect(request, request.url_for("admin.teams.list"))
