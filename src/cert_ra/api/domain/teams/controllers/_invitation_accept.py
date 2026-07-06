# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Invitation accept/decline controller."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from advanced_alchemy.exceptions import RepositoryError
from advanced_alchemy.extensions.litestar.providers import create_service_provider
from litestar import Controller, Request, get, post
from litestar.params import Parameter
from litestar.response import Redirect
from litestar_vite.inertia import InertiaRedirect, flash
from msgspec import Struct
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.teams.schemas import (
    InvitationAcceptPage,
    OidcProviderOption,
    TeamInvitationDetail,
)
from cert_ra.api.domain.teams.services import TeamInvitationService, TeamMemberService
from cert_ra.api.lib import crypt
from cert_ra.api.lib.invitations import (
    claim_invitation_accepted,
    claim_user_activation,
)
from cert_ra.api.lib.token_hashing import hmac_sha256
from cert_ra.db.models import TeamInvitation as TeamInvitationModel, User as UserModel
from cert_ra.db.models.team_invitation import InvitationKind

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

_ALLOWED_FORCE_PROVIDERS = frozenset({"google", "microsoft", "github"})
_PROVIDER_LABELS = {"google": "Google", "microsoft": "Microsoft", "github": "GitHub"}
_MIN_PASSWORD_LENGTH = 12

__all__ = ("InvitationAcceptController",)


class ActivationPasswordForm(Struct):
    """Password chosen by an invitee activating their account."""

    password: str
    confirm_password: str


def _oidc_options(request: Request) -> list[OidcProviderOption]:
    """Build the OIDC sign-in choices for the activation page."""
    return [
        OidcProviderOption(
            provider=provider,
            label=label,
            url=str(request.url_for("oidc.login", provider=provider)),
        )
        for provider, label in _PROVIDER_LABELS.items()
    ]


async def _activation_state(
    db: AsyncSession, user_id: UUID
) -> tuple[datetime | None, str | None] | None:
    """Load a pre-provisioned user's ``(activated_at, hashed_password)``.

    Selects the columns explicitly because ``hashed_password`` is a
    deferred (``security_sensitive``) attribute — touching it via a loaded
    ORM object would trigger an out-of-greenlet lazy load. Returns ``None``
    when no such user exists. ``activated_at`` may itself be ``None`` for a
    not-yet-activated account.
    """
    row = (
        await db.execute(
            select(UserModel.activated_at, UserModel.hashed_password).where(
                UserModel.id == user_id
            )
        )
    ).first()
    if row is None:
        return None
    return (row.activated_at, row.hashed_password)


class InvitationAcceptController(Controller):
    """Accept/Decline invitations (token-based, GET works without auth, POST requires auth)."""

    tags = ["Teams"]  # noqa: RUF012
    dependencies = {  # noqa: RUF012
        "team_invitations_service": create_service_provider(
            TeamInvitationService,
            load=[
                joinedload(TeamInvitationModel.team),
                joinedload(TeamInvitationModel.invited_by),
            ],
        ),
        "team_members_service": create_service_provider(TeamMemberService),
    }
    signature_namespace = {  # noqa: RUF012
        "TeamInvitationService": TeamInvitationService,
        "TeamMemberService": TeamMemberService,
        "ActivationPasswordForm": ActivationPasswordForm,
    }

    @get(
        component="invitation/accept",
        name="invitation.accept.page",
        operation_id="GetInvitationAcceptPage",
        path="/invitations/{token:str}/",
        exclude_from_auth=True,
    )
    async def get_invitation_page(  # noqa: PLR0911
        self,
        request: Request,
        team_invitations_service: TeamInvitationService,
        token: Annotated[
            str, Parameter(title="Token", description="The invitation token.")
        ],
    ) -> InvitationAcceptPage | Redirect:
        """Show invitation accept/decline page.

        Works for both authenticated and unauthenticated users.
        Unauthenticated users see the invitation details and can log in or sign up.

        If the invitation has ``force_provider`` set, redirects directly into
        the OIDC sign-in flow for that provider (per the OIDC SSO design's
        admin-driven provisioning path). The OIDC callback consumes the
        ``invitation_token_hash`` session key to atomically claim the invite.

        Returns:
            Invitation details and validity status, or a Redirect into the
            forced OIDC provider login.
        """
        invitation = await team_invitations_service.get_by_token(token)

        request.session["invitation_token"] = token
        request.session["invitation_token_hash"] = hmac_sha256(token)

        # The effective forced provider is the invitation's own
        # ``force_provider`` OR — once the team owner has locked sign-in —
        # the team's ``enforced_provider``. Either one suppresses the
        # password option and routes straight into that IdP.
        effective_provider: str | None = None
        if (
            invitation is not None
            and not invitation.is_expired
            and not invitation.is_accepted
        ):
            effective_provider = (
                invitation.force_provider or invitation.team.enforced_provider
            )
            if effective_provider in _ALLOWED_FORCE_PROVIDERS:
                return Redirect(
                    path=str(
                        request.url_for("oidc.login", provider=effective_provider)
                    ),
                )

        user_id = request.session.get("user_id")
        is_authenticated = user_id is not None
        current_user_email = user_id if is_authenticated else None

        login_url = str(request.url_for("login"))

        if invitation is None:
            return InvitationAcceptPage(
                invitation=TeamInvitationDetail(
                    id=UUID("00000000-0000-0000-0000-000000000000"),
                    team_name="",
                    team_slug="",
                    inviter_name="",
                    inviter_email="",
                    role="",
                ),
                is_valid=False,
                error_message="This invitation link is invalid or has been revoked.",
                is_authenticated=is_authenticated,
                is_correct_user=False,
                login_url=login_url,
            )

        inviter_name = (
            (
                invitation.invited_by.name
                if invitation.invited_by and invitation.invited_by.name
                else None
            )
            or invitation.invited_by_email
            or ""
        )
        base_detail = TeamInvitationDetail(
            id=invitation.id,
            team_name=invitation.team.name,
            team_slug=invitation.team.slug,
            inviter_name=inviter_name,
            inviter_email=invitation.invited_by_email,
            role=str(invitation.role),
            expires_at=invitation.expires_at,
        )

        if invitation.is_expired:
            return InvitationAcceptPage(
                invitation=TeamInvitationDetail(
                    **{**base_detail.to_dict(), "is_expired": True},
                ),
                is_valid=False,
                error_message="This invitation has expired.",
                is_authenticated=is_authenticated,
                is_correct_user=False,
                login_url=login_url,
            )

        if invitation.is_accepted:
            return InvitationAcceptPage(
                invitation=base_detail,
                is_valid=False,
                error_message="This invitation has already been accepted.",
                is_authenticated=is_authenticated,
                is_correct_user=False,
                login_url=login_url,
            )

        # First-time activation of an admin-provisioned account: render the
        # choice page (OIDC providers + set-a-password) instead of the
        # login/register prompt, which would dead-end (the email already
        # exists, but with no password). Suppressed above when the team
        # enforces a provider (we redirected straight into OIDC).
        if (
            not is_authenticated
            and invitation.kind == InvitationKind.FIRST_TIME_ACTIVATION
            and invitation.user_id is not None
        ):
            db_session = team_invitations_service.repository.session
            state = await _activation_state(db_session, invitation.user_id)
            if state is not None and state[0] is None and state[1] is None:
                return InvitationAcceptPage(
                    invitation=base_detail,
                    is_valid=True,
                    is_authenticated=False,
                    is_correct_user=False,
                    login_url=login_url,
                    is_activation=True,
                    allow_password=effective_provider is None,
                    invitee_email=invitation.email,
                    set_password_url=str(
                        request.url_for("invitation.set-password", token=token)
                    ),
                    oidc_options=_oidc_options(request),
                )

        if not is_authenticated:
            return InvitationAcceptPage(
                invitation=base_detail,
                is_valid=True,
                is_authenticated=False,
                is_correct_user=False,
                login_url=login_url,
            )

        is_correct_user = current_user_email == invitation.email
        if not is_correct_user:
            return InvitationAcceptPage(
                invitation=base_detail,
                is_valid=False,
                error_message=f"This invitation was sent to {invitation.email}. Please log in with that account.",
                is_authenticated=True,
                is_correct_user=False,
                login_url=login_url,
            )

        return InvitationAcceptPage(
            invitation=base_detail,
            is_valid=True,
            is_authenticated=True,
            is_correct_user=True,
            login_url=login_url,
        )

    @post(
        name="invitation.set-password",
        operation_id="ActivateInvitationWithPassword",
        path="/invitations/{token:str}/set-password",
        exclude_from_auth=True,
    )
    async def set_password(
        self,
        request: Request,
        team_invitations_service: TeamInvitationService,
        data: ActivationPasswordForm,
        token: Annotated[
            str, Parameter(title="Token", description="The invitation token.")
        ],
    ) -> InertiaRedirect:
        """Activate an admin-provisioned account with a password.

        Atomically writes the password hash and activates the
        pre-provisioned User (its team membership already exists from
        provisioning), marks the invitation accepted, and establishes a
        password session so the MFA enrollment trap forces MFA setup.
        Refused once the team enforces an OIDC provider.

        Returns:
            Redirect to the dashboard (then the MFA trap) on success, or
            back to the invitation page / login on any refusal.
        """
        invitation = await team_invitations_service.get_by_token(token)
        db_session = team_invitations_service.repository.session
        login = request.url_for("login")

        if (
            invitation is None
            or invitation.is_expired
            or invitation.is_accepted
            or invitation.kind != InvitationKind.FIRST_TIME_ACTIVATION
            or invitation.user_id is None
        ):
            flash(
                request,
                "This invitation link is invalid or has expired.",
                category="error",
            )
            return InertiaRedirect(request, login)

        accept_page = request.url_for("invitation.accept.page", token=token)

        # The team owner may have locked sign-in to an IdP after the
        # invite was sent — password activation is no longer permitted.
        if invitation.force_provider or invitation.team.enforced_provider:
            flash(
                request,
                "This organization now requires single sign-on. "
                "Continue with your provider.",
                category="error",
            )
            return InertiaRedirect(request, accept_page)

        state = await _activation_state(db_session, invitation.user_id)
        if state is None or state[0] is not None or state[1] is not None:
            flash(
                request,
                "This account is already set up. Please sign in.",
                category="info",
            )
            return InertiaRedirect(request, login)

        if (
            len(data.password) < _MIN_PASSWORD_LENGTH
            or data.password != data.confirm_password
        ):
            flash(
                request,
                f"Password must be at least {_MIN_PASSWORD_LENGTH} characters "
                "and match the confirmation.",
                category="error",
            )
            return InertiaRedirect(request, accept_page)

        hashed = await crypt.get_password_hash(data.password)
        claimed = await claim_user_activation(
            db_session, invitation.user_id, hashed_password=hashed
        )
        if not claimed:
            flash(
                request,
                "This account is already set up. Please sign in.",
                category="info",
            )
            return InertiaRedirect(request, login)
        await claim_invitation_accepted(db_session, invitation.id)
        await db_session.commit()

        # Establish a password session. ``mfa_enrolled=False`` makes the
        # MFA enrollment-trap middleware force MFA setup before anything
        # else. The invitee's email equals the pre-provisioned user's email.
        team_name = invitation.team.name
        request.set_session({"user_id": invitation.email})
        request.session["auth_method"] = "password"
        request.session["mfa_enrolled"] = False
        request.session.pop("invitation_token", None)
        request.session.pop("invitation_token_hash", None)
        flash(
            request,
            f"Welcome to {team_name}! Finish by setting up two-factor authentication.",
            category="success",
        )
        return InertiaRedirect(request, request.url_for("dashboard"))

    @post(
        name="invitation.accept",
        operation_id="AcceptInvitation",
        path="/invitations/{token:str}/accept",
        guards=[requires_active_user],
    )
    async def accept_invitation(
        self,
        request: Request,
        team_invitations_service: TeamInvitationService,
        team_members_service: TeamMemberService,
        current_user: UserModel,
        token: Annotated[
            str, Parameter(title="Token", description="The invitation token.")
        ],
    ) -> InertiaRedirect:
        """Accept a team invitation.

        Returns:
            Redirect to the team page.
        """
        invitation = await team_invitations_service.get_by_token(token)

        if invitation is None:
            flash(request, "Invalid invitation.", category="error")
            return InertiaRedirect(request, request.url_for("dashboard"))

        if current_user.email != invitation.email:
            flash(
                request,
                f"This invitation was sent to {invitation.email}.",
                category="error",
            )
            return InertiaRedirect(request, request.url_for("dashboard"))

        team_domain = invitation.team.domain
        if team_domain:
            invitee_domain = current_user.email.rsplit("@", 1)[-1].lower()
            if invitee_domain != team_domain:
                flash(
                    request,
                    f"This team only accepts members with @{team_domain} email addresses.",
                    category="error",
                )
                return InertiaRedirect(request, request.url_for("dashboard"))

        try:
            await team_invitations_service.accept_invitation(
                invitation=invitation,
                user=current_user,
                team_member_service=team_members_service,
            )
            request.session.pop("invitation_token", None)
            flash(request, f"You have joined {invitation.team.name}!", category="info")
            return InertiaRedirect(
                request, request.url_for("teams.show", team_slug=invitation.team.slug)
            )
        except RepositoryError as e:
            flash(request, str(e), category="error")
            return InertiaRedirect(request, request.url_for("dashboard"))

    @post(
        name="invitation.decline",
        operation_id="DeclineInvitation",
        path="/invitations/{token:str}/decline",
        guards=[requires_active_user],
    )
    async def decline_invitation(
        self,
        request: Request,
        team_invitations_service: TeamInvitationService,
        current_user: UserModel,
        token: Annotated[
            str, Parameter(title="Token", description="The invitation token.")
        ],
    ) -> InertiaRedirect:
        """Decline a team invitation.

        Returns:
            Redirect to dashboard.
        """
        invitation = await team_invitations_service.get_by_token(token)

        if invitation is None:
            flash(request, "Invalid invitation.", category="error")
            return InertiaRedirect(request, request.url_for("dashboard"))

        if current_user.email != invitation.email:
            flash(
                request,
                f"This invitation was sent to {invitation.email}.",
                category="error",
            )
            return InertiaRedirect(request, request.url_for("dashboard"))

        await team_invitations_service.delete(invitation.id)
        request.session.pop("invitation_token", None)
        flash(request, "Invitation declined.", category="info")
        return InertiaRedirect(request, request.url_for("dashboard"))
