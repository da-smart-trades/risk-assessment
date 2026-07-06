# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Admin recovery actions controller — Reset MFA Only + Total Recovery.

Both actions are team-scoped: the acting admin must own/admin the
team the target user belongs to. Authorization gating is the same
``AdminTeamController.guards`` pattern (operator_support split lands
in PR-8).

Reset MFA Only (design Fix 6 — lighter):
- Wipes ``UserPasskey`` + ``UserRecoveryCode`` rows + clears
  ``totp_secret`` / ``is_two_factor_enabled`` / ``backup_codes``.
- Leaves ``hashed_password`` + ``UserOauthAccount`` + lockout state
  untouched. The user signs in with their existing password and
  hits the enrollment trap.

Total Recovery (heavier):
- Everything from Reset MFA Only, PLUS mints a
  ``UserPasswordResetToken`` and emits the recovery email so the
  user can choose a new password.
- Preserves ``User.activated_at`` (the invitation is dead — design
  #136).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003

from advanced_alchemy.extensions.litestar.providers import create_service_provider
from litestar import Controller, Request, post
from litestar.di import Provide
from litestar.params import Parameter
from litestar_vite.inertia import InertiaRedirect, flash
from sqlalchemy import delete

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.admin.dependencies import provide_audit_service
from cert_ra.api.domain.admin.services import AuditLogService
from cert_ra.api.domain.teams.dependencies import (
    provide_team_members_service,
)
from cert_ra.api.domain.teams.guards import requires_operator_tenant_admin
from cert_ra.api.domain.teams.services import TeamMemberService, TeamService
from cert_ra.api.lib.operator_audit import (
    OperatorAction,
    emit_operator_audit_fanout,
    record_operator_action,
)
from cert_ra.api.lib.token_hashing import hmac_sha256
from cert_ra.db.models import (
    AuditAction,
    User,
    UserPasskey,
    UserPasswordResetToken,
    UserRecoveryCode,
)

if TYPE_CHECKING:
    from cert_ra.db.models import User as UserModel

__all__ = ("AdminRecoveryController",)

PASSWORD_RESET_TOKEN_TTL = timedelta(hours=1)


class AdminRecoveryController(Controller):
    """Admin MFA / total-recovery actions on a team member."""

    path = "/admin/teams"
    tags = ["Admin"]  # noqa: RUF012
    include_in_schema = False
    cache = False
    guards = [requires_operator_tenant_admin]  # noqa: RUF012
    dependencies = {  # noqa: RUF012
        "users_service": Provide(provide_users_service),
        "teams_service": create_service_provider(TeamService),
        "team_members_service": Provide(provide_team_members_service),
        "audit_service": Provide(provide_audit_service),
    }
    signature_namespace = {  # noqa: RUF012
        "UserService": UserService,
        "TeamService": TeamService,
        "TeamMemberService": TeamMemberService,
        "AuditLogService": AuditLogService,
    }

    @post(
        name="admin.teams.members.reset_mfa_only",
        operation_id="AdminResetMfaOnly",
        path="/{team_id:uuid}/members/{member_id:uuid}/reset-mfa/",
        status_code=303,
    )
    async def reset_mfa_only(
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
        """Reset MFA factors only — leave password + OAuth untouched.

        Clears UserPasskey, UserRecoveryCode, totp_secret,
        is_two_factor_enabled, backup_codes. Preserves hashed_password,
        UserOauthAccount, UserLockout, activated_at.
        """
        team = await teams_service.get(team_id)
        member = await team_members_service.get(member_id)
        db_session = users_service.repository.session

        await _wipe_mfa_factors(db_session, user_id=member.user_id)
        # Operator audit row, synchronous in the action's transaction.
        await record_operator_action(
            db_session,
            request=request,
            actor=current_user,
            action=OperatorAction.RESET_MFA_ONLY,
            target_team_id=team.id,
            target_user_id=member.user_id,
            payload={"team_name": team.name},
        )
        await db_session.commit()

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.USER_UPDATED,
            target_type="user",
            target_id=member.user_id,
            target_label=team.name,
            details={"recovery_action": "reset_mfa_only", "team_id": str(team_id)},
            ip_address=request.client.host if request.client else None,
        )
        emit_operator_audit_fanout(
            request,
            action=OperatorAction.RESET_MFA_ONLY,
            actor_email=current_user.email,
            target_team_name=team.name,
            security_contact_email=team.security_contact_email,
        )
        request.app.emit(
            "admin_mfa_reset",
            user_id=member.user_id,
            team_name=team.name,
            actor_role="admin",
        )
        flash(
            request,
            "MFA factors reset; user retains their password.",
            category="success",
        )
        return InertiaRedirect(
            request, request.url_for("admin.teams.detail", team_id=team_id)
        )

    @post(
        name="admin.teams.members.total_recovery",
        operation_id="AdminTotalRecovery",
        path="/{team_id:uuid}/members/{member_id:uuid}/total-recovery/",
        status_code=303,
    )
    async def total_recovery(
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
        """Total Recovery — wipe MFA AND issue a password-reset link.

        Goes beyond Reset MFA Only by minting a
        UserPasswordResetToken and emitting an email that lets the
        user set a new password. Preserves ``activated_at`` so the
        original invitation doesn't reappear as an open one.
        """
        team = await teams_service.get(team_id)
        member = await team_members_service.get(member_id)
        db_session = users_service.repository.session

        await _wipe_mfa_factors(db_session, user_id=member.user_id)

        plain_token = secrets.token_urlsafe(32)
        token_row = UserPasswordResetToken(
            user_id=member.user_id,
            token_hash=hmac_sha256(plain_token),
            expires_at=datetime.now(UTC) + PASSWORD_RESET_TOKEN_TTL,
        )
        db_session.add(token_row)
        await record_operator_action(
            db_session,
            request=request,
            actor=current_user,
            action=OperatorAction.TOTAL_RECOVERY,
            target_team_id=team.id,
            target_user_id=member.user_id,
            payload={"team_name": team.name},
        )
        await db_session.commit()

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.USER_PASSWORD_RESET,
            target_type="user",
            target_id=member.user_id,
            target_label=team.name,
            details={"recovery_action": "total_recovery", "team_id": str(team_id)},
            ip_address=request.client.host if request.client else None,
        )
        emit_operator_audit_fanout(
            request,
            action=OperatorAction.TOTAL_RECOVERY,
            actor_email=current_user.email,
            target_team_name=team.name,
            security_contact_email=team.security_contact_email,
        )
        request.app.emit(
            "admin_total_recovery",
            user_id=member.user_id,
            team_name=team.name,
            actor_role="admin",
            token=plain_token,
        )
        flash(
            request,
            "Total Recovery issued — password reset email sent to the user.",
            category="success",
        )
        return InertiaRedirect(
            request, request.url_for("admin.teams.detail", team_id=team_id)
        )


async def _wipe_mfa_factors(db_session: object, *, user_id: UUID) -> None:
    """Clear every MFA factor for ``user_id`` without touching anything else.

    Inlined here rather than added to ``lib/mfa/`` because this is the
    only path that legitimately drops every factor at once. Self-service
    factor removal goes through the per-factor endpoints in PR-3.
    """
    from sqlalchemy.orm import undefer_group

    await db_session.execute(  # type: ignore[attr-defined]
        delete(UserPasskey).where(UserPasskey.user_id == user_id)
    )
    await db_session.execute(  # type: ignore[attr-defined]
        delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id)
    )
    # Bring totp_secret + backup_codes into the session so the UPDATE
    # below covers their deferred-group columns.
    user = await db_session.scalar(  # type: ignore[attr-defined]
        User.__table__.select().where(User.id == user_id)
    )
    if user is None:
        return
    from sqlalchemy import update

    await db_session.execute(  # type: ignore[attr-defined]
        update(User)
        .where(User.id == user_id)
        .values(
            totp_secret=None,
            is_two_factor_enabled=False,
            two_factor_confirmed_at=None,
            backup_codes=None,
        )
    )
    _ = undefer_group  # keep import used
