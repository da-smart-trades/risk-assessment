# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Operator support → tenant-admin promotion (PR-8).

Promotion is a sensitive operation: it grants cross-customer write
power. It requires an existing ``operator_tenant_admin`` (the approver,
enforced by ``requires_operator_tenant_admin``) and is recorded in the
operator audit log.

Fresh re-auth (AC #30): on promotion the promoted user's existing
sessions are invalidated, so the elevated role only takes effect after
they sign in again (a fresh session + operator passkey MFA via Control
1). Combined with the approver guard (an existing tenant-admin must
perform the promotion) this satisfies "approval AND fresh sign-in".

Operator team hardening — Control 2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID  # noqa: TC003

from litestar import Controller, Request, get, post
from litestar.di import Provide
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from litestar_vite.inertia import InertiaRedirect, flash
from sqlalchemy import select

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.teams.guards import requires_operator_tenant_admin
from cert_ra.api.lib.operator_audit import (
    emit_operator_audit_fanout,
    record_operator_action,
)
from cert_ra.api.lib.session_rotation import invalidate_other_user_sessions
from cert_ra.db.models import Team, TeamMember, TeamRoles

if TYPE_CHECKING:
    from cert_ra.db.models import User as UserModel

__all__ = ("OperatorPromotionController",)

_PROMOTE_ACTION = "promote_to_tenant_admin"


class OperatorPromotionController(Controller):
    """Promote an operator_support member to operator_tenant_admin."""

    path = "/admin/operator"
    tags = ["Admin"]  # noqa: RUF012
    include_in_schema = False
    cache = False
    guards = [requires_operator_tenant_admin]  # noqa: RUF012
    dependencies = {  # noqa: RUF012
        "users_service": Provide(provide_users_service),
    }
    signature_namespace = {"UserService": UserService}  # noqa: RUF012

    @get(
        component="admin/operator/promotion",
        name="admin.operator.promotion.page",
        path="/promotion/",
    )
    async def show(
        self,
        users_service: UserService,
    ) -> dict[str, Any]:
        """List operator-team members + roles for the promotion UI."""
        db = users_service.repository.session
        team = await db.scalar(select(Team).where(Team.is_operator.is_(True)).limit(1))
        if team is None:
            return {"teamName": None, "members": []}
        members = (
            await db.execute(select(TeamMember).where(TeamMember.team_id == team.id))
        ).scalars()
        return {
            "teamName": team.name,
            "members": [
                {
                    "memberId": str(m.id),
                    "email": m.email,
                    "name": m.name,
                    "role": "owner" if m.is_owner else str(m.role),
                    "isOwner": m.is_owner,
                    "canPromote": (not m.is_owner)
                    and str(m.role) == TeamRoles.OPERATOR_SUPPORT.value,
                }
                for m in members
            ],
        }

    @post(
        name="admin.operator.promote",
        path="/members/{member_id:uuid}/promote/",
        status_code=303,
    )
    async def promote(
        self,
        request: Request,
        users_service: UserService,
        current_user: UserModel,
        member_id: Annotated[UUID, Parameter(title="Member ID")],
    ) -> InertiaRedirect:
        """Promote an operator-team member to ``operator_tenant_admin``."""
        db = users_service.repository.session
        member = await db.get(TeamMember, member_id)
        if member is None:
            raise NotFoundException("Member not found")
        team = await db.get(Team, member.team_id)
        if team is None or not team.is_operator:
            raise NotFoundException("Not an operator-team member")

        promoted_email = member.email
        member.role = TeamRoles.OPERATOR_TENANT_ADMIN
        await record_operator_action(
            db,
            request=request,
            actor=current_user,
            action=_PROMOTE_ACTION,
            target_team_id=team.id,
            target_user_id=member.user_id,
            payload={"role": TeamRoles.OPERATOR_TENANT_ADMIN.value},
        )
        await db.commit()

        # Fresh re-auth gate (AC #30): kill the promoted user's existing
        # sessions so the elevated role only takes effect after they sign
        # in again (fresh session + operator passkey MFA via Control 1).
        await invalidate_other_user_sessions(
            db, user_email=promoted_email, current_session_key=None
        )
        await db.commit()

        emit_operator_audit_fanout(
            request,
            action=_PROMOTE_ACTION,
            actor_email=current_user.email,
            target_team_name=team.name,
            security_contact_email=team.security_contact_email,
        )
        flash(
            request,
            "Member promoted to operator tenant-admin. They must sign in "
            "again for the new role to take effect.",
            category="success",
        )
        return InertiaRedirect(request, request.url_for("admin.dashboard"))
