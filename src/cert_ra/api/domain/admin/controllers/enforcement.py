# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Per-team ``enforced_provider`` set/unset controller.

A team **owner** (or an operator ``operator_tenant_admin``, proxied here
by ``is_superuser`` until the role split lands in PR-8) can lock their
team to a single OIDC provider, or unlock it again.

Setting is guarded by two hard preconditions (design — Setting /
unsetting):

1. The acting user is currently signed in via the target provider
   (``session["auth_method"] == provider``). Proves they can *currently*
   sign in there, not merely that a stale link exists. Else ``409``.
2. Every *other* team owner has a ``UserOauthAccount`` for the target
   provider. Else ``409`` listing the non-conforming owners.

Unsetting (back to NULL) is owner-gated too but has no preconditions —
relaxing the policy can't lock anyone out.

The whole surface is dark while ``cert_ra_features_enforced_provider``
is off: GET and POST both return ``404``.

Per-team IDP enforcement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

from litestar import Controller, Request, get, post
from litestar.di import Provide
from litestar.exceptions import (
    HTTPException,
    NotFoundException,
    PermissionDeniedException,
)
from litestar.params import Parameter
from litestar.status_codes import HTTP_409_CONFLICT
from litestar_vite.inertia import InertiaRedirect, flash
from msgspec import Struct
from sqlalchemy import select

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.admin.dependencies import provide_audit_service
from cert_ra.api.domain.admin.services import AuditLogService
from cert_ra.api.lib.oidc.providers import Provider, load_provider_configs
from cert_ra.api.lib.operator_roles import is_operator_tenant_admin
from cert_ra.api.lib.session_rotation import reauthenticate_session
from cert_ra.api.lib.team_policy import find_stuck_members
from cert_ra.db.models import (
    AuditAction,
    AuditLog,
    Team,
    TeamMember,
    TeamRoles,
    UserOauthAccount,
)
from cert_ra.settings.api import get_feature_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from cert_ra.db.models import User as UserModel

__all__ = ("EnforcementController",)

_VALID_PROVIDERS = frozenset({"google", "microsoft", "github"})


class EnforcementForm(Struct):
    """POST payload for set/unset.

    ``provider`` is the provider value to enforce, or an empty string /
    ``None`` to unset.
    """

    provider: str | None = None


class ReminderForm(Struct):
    """POST payload for the stuck-member reminder action."""

    member_id: UUID


class EnforcementController(Controller):
    """Owner-driven ``enforced_provider`` management for a team."""

    path = "/teams"
    include_in_schema = False
    cache = False
    dependencies = {  # noqa: RUF012
        "users_service": Provide(provide_users_service),
        "audit_service": Provide(provide_audit_service),
    }
    signature_namespace = {  # noqa: RUF012
        "UserService": UserService,
        "AuditLogService": AuditLogService,
        "EnforcementForm": EnforcementForm,
        "ReminderForm": ReminderForm,
    }

    @get(
        component="team/enforcement",
        name="team.enforcement.show",
        path="/{team_id:uuid}/enforcement/",
    )
    async def show(
        self,
        request: Request,
        users_service: UserService,
        current_user: UserModel,
        team_id: Annotated[UUID, Parameter(title="Team ID")],
    ) -> dict[str, Any]:
        """Render the enforcement settings page (owner / operator only)."""
        _require_flag_on()
        db = users_service.repository.session
        team = await _get_team_or_404(db, team_id)
        await _require_can_manage(db, current_user, team_id)
        return {
            "teamId": str(team.id),
            "teamName": team.name,
            "enforcedProvider": team.enforced_provider,
            "currentAuthMethod": request.session.get("auth_method"),
            "availableProviders": _available_providers(),
            "stuckMembers": await find_stuck_members(db, team_id),
        }

    @get(
        name="team.enforcement.stuck",
        path="/{team_id:uuid}/enforcement/stuck/",
    )
    async def stuck(
        self,
        users_service: UserService,
        current_user: UserModel,
        team_id: Annotated[UUID, Parameter(title="Team ID")],
    ) -> dict[str, Any]:
        """Return members who can't self-migrate (owner / admin / operator)."""
        _require_flag_on()
        db = users_service.repository.session
        await _get_team_or_404(db, team_id)
        await _require_can_view_stuck(db, current_user, team_id)
        return {"stuckMembers": await find_stuck_members(db, team_id)}

    @post(
        name="team.enforcement.remind",
        path="/{team_id:uuid}/enforcement/remind/",
        status_code=303,
    )
    async def remind(
        self,
        request: Request,
        users_service: UserService,
        audit_service: AuditLogService,
        current_user: UserModel,
        data: ReminderForm,
        team_id: Annotated[UUID, Parameter(title="Team ID")],
    ) -> InertiaRedirect:
        """Email a stuck member a reminder to migrate (owner / admin).

        Throttled to one reminder per member per 48h (design — stuck
        list, open question #2). The throttle window is enforced against
        the audit log so it survives restarts and spans workers.
        """
        _require_flag_on()
        db = users_service.repository.session
        team = await _get_team_or_404(db, team_id)
        await _require_can_view_stuck(db, current_user, team_id)

        # Only remind members who are genuinely stuck, so the action
        # can't be abused to mail arbitrary users.
        stuck = {m["id"]: m for m in await find_stuck_members(db, team_id)}
        member = stuck.get(str(data.member_id))
        if member is None or team.enforced_provider is None:
            flash(request, "That member no longer needs a reminder.", category="info")
        elif await _reminded_within_window(db, UUID(str(member["id"]))):
            flash(
                request,
                "A reminder was already sent in the last 48 hours.",
                category="info",
            )
        else:
            request.app.emit(
                "enforcement_reminder",
                user_email=member["email"],
                team_name=team.name,
                provider=team.enforced_provider,
            )
            await audit_service.log_action(
                actor=current_user,
                action=AuditAction.TEAM_ENFORCEMENT_REMINDER,
                target_type="user",
                target_id=UUID(str(member["id"])),
                target_label=team.name,
                details={"team_id": str(team_id)},
                ip_address=request.client.host if request.client else None,
            )
            flash(request, "Reminder sent.", category="success")
        return InertiaRedirect(
            request, request.url_for("team.enforcement.show", team_id=team_id)
        )

    @post(
        name="team.enforcement.update",
        path="/{team_id:uuid}/enforcement/",
        status_code=303,
    )
    async def update(
        self,
        request: Request,
        users_service: UserService,
        audit_service: AuditLogService,
        current_user: UserModel,
        data: EnforcementForm,
        team_id: Annotated[UUID, Parameter(title="Team ID")],
    ) -> InertiaRedirect:
        """Set or unset ``enforced_provider`` for ``team_id``."""
        _require_flag_on()
        db = users_service.repository.session
        team = await _get_team_or_404(db, team_id)
        await _require_can_manage(db, current_user, team_id)

        provider = (data.provider or "").strip().lower() or None
        if provider is None:
            await self._unset(request, db, audit_service, current_user, team)
        else:
            await self._set(request, db, audit_service, current_user, team, provider)

        # enforced_provider change is NOT a credential change for the
        # acting user — rotate their session only (design #9).
        await reauthenticate_session(request, db, rotate_only=True)
        return InertiaRedirect(
            request, request.url_for("team.enforcement.show", team_id=team_id)
        )

    async def _set(
        self,
        request: Request,
        db: AsyncSession,
        audit_service: AuditLogService,
        current_user: UserModel,
        team: Team,
        provider: str,
    ) -> None:
        """Apply the two preconditions, then set ``enforced_provider``."""
        if provider not in _VALID_PROVIDERS:
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail=f"Unknown provider {provider!r}.",
            )

        # Precondition 1: acting user signed in via the target provider.
        if request.session.get("auth_method") != provider:
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail=(
                    f"Sign in via {_label(provider)} to enforce {_label(provider)}."
                ),
                extra={"reason": "acting_user_wrong_provider"},
            )

        # Precondition 2: every OTHER owner has the target provider linked.
        non_conforming = await _owners_missing_provider(
            db, team_id=team.id, provider=provider, exclude_user_id=current_user.id
        )
        if non_conforming:
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail=(
                    "These team owners must link "
                    f"{_label(provider)} first: " + ", ".join(non_conforming)
                ),
                extra={
                    "reason": "co_owner_not_conforming",
                    "owners": non_conforming,
                },
            )

        team.enforced_provider = provider
        team.enforced_provider_set_at = datetime.now(UTC)
        await db.commit()

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.TEAM_UPDATED,
            target_type="team",
            target_id=team.id,
            target_label=team.name,
            details={"enforced_provider": provider},
            ip_address=request.client.host if request.client else None,
        )
        request.app.emit(
            "team_enforced_provider_set",
            team_id=team.id,
            team_name=team.name,
            provider=provider,
        )
        flash(
            request,
            f"This team now requires {_label(provider)} sign-in.",
            category="success",
        )

    async def _unset(
        self,
        request: Request,
        db: AsyncSession,
        audit_service: AuditLogService,
        current_user: UserModel,
        team: Team,
    ) -> None:
        """Clear ``enforced_provider`` — no preconditions."""
        team.enforced_provider = None
        team.enforced_provider_set_at = None
        await db.commit()

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.TEAM_UPDATED,
            target_type="team",
            target_id=team.id,
            target_label=team.name,
            details={"enforced_provider": None},
            ip_address=request.client.host if request.client else None,
        )
        request.app.emit(
            "team_enforced_provider_unset",
            team_id=team.id,
            team_name=team.name,
        )
        flash(
            request,
            "Sign-in provider enforcement removed for this team.",
            category="success",
        )


_REMINDER_THROTTLE = timedelta(hours=48)


async def _reminded_within_window(db: AsyncSession, user_id: UUID) -> bool:
    """True if the user was reminded within the last 48h.

    Backed by the audit log so the throttle is durable and shared across
    workers without a dedicated table.
    """
    cutoff = datetime.now(UTC) - _REMINDER_THROTTLE
    recent = await db.scalar(
        select(AuditLog.id)
        .where(
            AuditLog.action == AuditAction.TEAM_ENFORCEMENT_REMINDER.value,
            AuditLog.target_id == user_id,
            AuditLog.created_at > cutoff,
        )
        .limit(1)
    )
    return recent is not None


def _require_flag_on() -> None:
    """Raise 404 unless ``cert_ra_features_enforced_provider`` is on."""
    if not get_feature_settings().enforced_provider:
        raise NotFoundException("Not found")


async def _get_team_or_404(db: AsyncSession, team_id: UUID) -> Team:
    """Load a team or raise 404."""
    team = await db.get(Team, team_id)
    if team is None:
        raise NotFoundException("Team not found")
    return team


async def _require_can_manage(db: AsyncSession, user: UserModel, team_id: UUID) -> None:
    """Raise 403 unless ``user`` owns the team or is an operator tenant-admin.

    ``operator_tenant_admin`` (and superusers) may set enforcement on any
    customer team for incident response (PR-8, Control 2).
    """
    if user.is_superuser or await is_operator_tenant_admin(db, user):
        return
    is_owner = await db.scalar(
        select(TeamMember.id).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user.id,
            TeamMember.is_owner.is_(True),
        )
    )
    if is_owner is None:
        raise PermissionDeniedException(
            "Only team owners can change sign-in enforcement."
        )


async def _require_can_view_stuck(
    db: AsyncSession, user: UserModel, team_id: UUID
) -> None:
    """Raise 403 unless ``user`` is an owner, admin, or operator admin.

    Broader than ``_require_can_manage``: the stuck list is a read-only
    view team admins also need (design #110/#111), while only owners may
    flip the policy.
    """
    if user.is_superuser or await is_operator_tenant_admin(db, user):
        return
    membership = await db.scalar(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user.id,
        )
    )
    if membership is not None and (
        membership.is_owner or membership.role == TeamRoles.ADMIN
    ):
        return
    raise PermissionDeniedException(
        "Only team owners and admins can view the stuck-members list."
    )


async def _owners_missing_provider(
    db: AsyncSession,
    *,
    team_id: UUID,
    provider: str,
    exclude_user_id: UUID,
) -> list[str]:
    """Return emails of other team owners lacking ``provider`` linkage."""
    owner_rows = await db.execute(
        select(TeamMember.user_id).where(
            TeamMember.team_id == team_id,
            TeamMember.is_owner.is_(True),
            TeamMember.user_id != exclude_user_id,
        )
    )
    missing: list[str] = []
    for (user_id,) in owner_rows:
        linked = await db.scalar(
            select(UserOauthAccount.id).where(
                UserOauthAccount.user_id == user_id,
                UserOauthAccount.oauth_name == provider,
            )
        )
        if linked is None:
            member = await db.scalar(
                select(TeamMember).where(
                    TeamMember.team_id == team_id,
                    TeamMember.user_id == user_id,
                )
            )
            missing.append(member.email if member is not None else str(user_id))
    return missing


def _available_providers() -> list[str]:
    """Provider values configured (sign-in-able) on this deployment."""
    configs = load_provider_configs()
    available: list[str] = []
    for prov in Provider:
        cfg = configs.get(prov)
        if cfg is not None and cfg.client_id and cfg.client_secret:
            available.append(prov.value)
    return available


def _label(provider: str) -> str:
    """Human-readable provider label."""
    return {
        "google": "Google",
        "microsoft": "Microsoft",
        "github": "GitHub",
    }.get(provider, provider.capitalize())
