# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""New-flow OIDC sign-in controller.

Distinct from the legacy ``_registration.py`` GitHub/Google routes at
``/o/<provider>/complete/`` which auto-create users. This controller
mounts the new ``/auth/<provider>/login`` + ``/auth/<provider>/callback``
routes that enforce the admin-driven provisioning rule: the OIDC
resolver refuses to create a User row from token claims.

Both paths coexist in PR-2a. A later PR decommissions the legacy
``/o/`` routes.

Session keys set on successful sign-in (alongside the existing
``user_id`` email convention):
- ``user_id``: user.email (matches the legacy
  ``current_user_from_session`` lookup).
- ``auth_method``: provider value ("google" | "microsoft" | "github").
- ``last_auth_at``: ISO UTC timestamp of the sign-in completion.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from cert_ra.db.models import User


from authlib.integrations.httpx_client import AsyncOAuth2Client
from litestar import Controller, Request, get
from litestar.di import Provide
from litestar.exceptions import NotAuthorizedException
from litestar.params import Parameter
from litestar.response import Redirect
from litestar_vite.inertia import InertiaRedirect, flash

from cert_ra.api.domain.accounts.controllers._link_confirm import (
    issue_pending_link_cookie,
    mint_pending_oidc_link,
)
from cert_ra.api.domain.accounts.controllers.enforcement_migration import (
    complete_provider_switch,
    has_pending_switch_cookie,
    start_provider_switch,
)
from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.services import (
    OidcIdentityResolver,
    PendingLinkRequired,
    ProviderNotPermittedError,
    RootCannotUseIdpError,
    UnknownUserError,
    UserService,
    WrongProviderError,
)
from cert_ra.api.lib.auth_lockout import (
    OperatorPostureError,
    assert_operator_mfa_posture,
)
from cert_ra.api.lib.invitations import (
    InvitationUnusableError,
    assert_invitation_usable,
    claim_invitation_accepted,
    find_invitation_by_token_hash,
)
from cert_ra.api.lib.oidc.identity import (
    ExtractedIdentity,
    IdentityError,
    extract_identity,
)
from cert_ra.api.lib.oidc.providers import (
    Provider,
    ProviderConfig,
    get_discovery,
    load_provider_configs,
)
from cert_ra.api.lib.safe_redirect import safe_redirect_target

__all__ = ("OidcController",)

# Session key for the in-flight OIDC flow's state (PKCE verifier,
# nonce, state token, intended redirect_to). Single key so we can
# pop the whole dict atomically.
_FLOW_SESSION_KEY = "oidc_flow"


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE (verifier, S256 challenge) pair.

    Returns:
        ``(verifier, challenge)`` — verifier is held server-side in
        the session for the token-exchange; challenge is sent in the
        authorize URL.
    """
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _redirect_uri(request: Request, provider: Provider) -> str:
    """Build the absolute callback URL for ``provider``."""
    return str(request.url_for("oidc.callback", provider=provider.value))


def _stash_auth_flow(
    request: Request,
    *,
    page: str,
    context: dict[str, Any],
) -> None:
    """Stash a landing-page directive in the session.

    Read by the (PR-6) landing-page controllers. Until PR-6 lands the
    actual pages, this also serves as a marker for log inspection.
    """
    request.session["auth_flow"] = {"page": page, "context": context}


class OidcController(Controller):
    """New-flow OIDC sign-in (Google, Microsoft, GitHub).

    Coexists with the legacy ``_registration.py`` OAuth2 routes. The
    key difference: this controller refuses to auto-create users.
    Admin-driven provisioning is enforced via the resolver's
    ``UnknownUserError``.
    """

    include_in_schema = False
    dependencies = {  # noqa: RUF012
        "users_service": Provide(provide_users_service),
    }
    signature_namespace = {"UserService": UserService}  # noqa: RUF012
    cache = False
    exclude_from_auth = True

    @get(name="oidc.login", path="/auth/{provider:str}/login")
    async def login(
        self,
        request: Request,
        provider: str,
        redirect_to: str | None = Parameter(query="redirect_to", required=False),
    ) -> Redirect:
        """Start the OIDC authorization-code flow.

        Generates state + nonce + PKCE, stashes them in the session,
        and redirects to the IdP's authorize endpoint.
        """
        try:
            prov = Provider(provider)
        except ValueError as exc:
            raise NotAuthorizedException("Unknown provider") from exc
        cfg = _provider_config_or_raise(prov)

        if cfg.discovery_url is not None:
            discovery = await get_discovery(cfg.discovery_url)
            authorize_endpoint = discovery["authorization_endpoint"]
        else:
            authorize_endpoint = cfg.authorize_endpoint
        if authorize_endpoint is None:
            raise NotAuthorizedException("Provider authorize endpoint missing")

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier, challenge = _pkce_pair()

        request.session[_FLOW_SESSION_KEY] = {
            "provider": prov.value,
            "state": state,
            "nonce": nonce,
            "code_verifier": verifier,
            "redirect_to": safe_redirect_target(redirect_to),
        }

        params = {
            "client_id": cfg.client_id,
            "redirect_uri": _redirect_uri(request, prov),
            "response_type": "code",
            "scope": " ".join(cfg.scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **cfg.extra_authorize_params,
        }
        # Only OIDC providers honor the nonce parameter.
        if cfg.discovery_url is not None:
            params["nonce"] = nonce

        return Redirect(f"{authorize_endpoint}?{urlencode(params)}")

    @get(name="oidc.callback", path="/auth/{provider:str}/callback")
    async def callback(
        self,
        request: Request,
        users_service: UserService,
        provider: str,
        code: str | None = Parameter(query="code", required=False),
        callback_state: str | None = Parameter(query="state", required=False),
        callback_error: str | None = Parameter(query="error", required=False),
    ) -> InertiaRedirect | Redirect:
        """Handle the IdP's authorization-code redirect.

        Validates state + the token (via ``extract_identity``), runs
        the resolver, and either signs the user in or routes to a
        landing page.
        """
        flow = request.session.pop(_FLOW_SESSION_KEY, None)
        if (
            callback_error
            or code is None
            or callback_state is None
            or flow is None
            or flow.get("provider") != provider
            or flow.get("state") != callback_state
        ):
            raise NotAuthorizedException("Invalid OIDC state")

        try:
            prov = Provider(provider)
        except ValueError as exc:
            raise NotAuthorizedException("Unknown provider") from exc
        cfg = _provider_config_or_raise(prov)

        if cfg.discovery_url is not None:
            discovery = await get_discovery(cfg.discovery_url)
            token_endpoint = discovery["token_endpoint"]
        else:
            token_endpoint = cfg.token_endpoint
        if token_endpoint is None:
            raise NotAuthorizedException("Provider token endpoint missing")

        async with AsyncOAuth2Client(  # pyright: ignore[reportGeneralTypeIssues]
            client_id=cfg.client_id,
            client_secret=cfg.client_secret,
            redirect_uri=_redirect_uri(request, prov),
        ) as client:
            # code_verifier must be passed to fetch_token (not the client
            # constructor): authlib's _prepare_token_endpoint_body for the
            # authorization_code grant builds the POST body from fetch_token
            # kwargs only and never reads self.code_verifier — Google then
            # rejects the exchange with "invalid_grant: Missing code verifier"
            # once PKCE is enforced on the OAuth client.
            token = await client.fetch_token(
                token_endpoint,
                code=code,
                grant_type="authorization_code",
                code_verifier=flow["code_verifier"],
            )

        try:
            identity = await extract_identity(
                prov,
                cfg,
                token,
                expected_nonce=flow["nonce"],
                client_id=cfg.client_id,
            )
        except IdentityError as exc:
            return await _route_identity_error(request, exc, prov)

        # If a provider-switch is in flight, this callback is the
        # required-provider leg of the enforcement self-migration, not a
        # normal sign-in. Complete the swap instead of re-resolving.
        raw_switch = has_pending_switch_cookie(request)
        if raw_switch is not None:
            return await complete_provider_switch(
                request,
                users_service.repository.session,
                identity,
                raw_token=raw_switch,
                redirect_to=flow["redirect_to"],
            )

        return await self._resolve_and_signin(
            request,
            users_service,
            identity,
            redirect_to=flow["redirect_to"],
        )

    async def _resolve_and_signin(  # noqa: PLR0911 — one branch per resolver outcome
        self,
        request: Request,
        users_service: UserService,
        identity: ExtractedIdentity,
        *,
        redirect_to: str,
    ) -> InertiaRedirect | Redirect:
        """Resolve ``identity`` to a User and establish the session.

        Catches the resolver's structured exceptions and routes each
        to a landing page (placeholders until PR-6 wires the pages).
        """
        db_session = users_service.repository.session
        resolver = OidcIdentityResolver(db_session)
        try:
            user = await resolver.resolve(identity)
        except RootCannotUseIdpError:
            # The break-glass root must use password sign-in only.
            flash(
                request,
                "This account signs in with a password, not single sign-on.",
                category="error",
            )
            return Redirect(request.url_for("login"))
        except UnknownUserError:
            _stash_auth_flow(
                request,
                page="invitation_required",
                context={"provider": identity.provider.value},
            )
            return Redirect(request.url_for("auth.invitation-required"))
        except WrongProviderError as exc:
            _stash_auth_flow(
                request,
                page="wrong_provider",
                context={
                    "existing": exc.existing_provider,
                    "attempted": exc.attempted_provider,
                },
            )
            return Redirect(request.url_for("auth.wrong-provider"))
        except ProviderNotPermittedError as exc:
            # The user authenticated via their linked provider, but a
            # team now enforces a different one. If that provider is
            # available on this deployment, send them through the
            # self-migration flow (stash this identity, bounce to the
            # required provider). Otherwise dead-end at /auth/team-policy.
            if _provider_available(exc.required_provider):
                return await start_provider_switch(
                    request,
                    db_session,
                    target_user_id=exc.target_user_id,
                    source_identity=identity,
                    required_provider=exc.required_provider,
                )
            _stash_auth_flow(
                request,
                page="team_policy",
                context={"required": exc.required_provider},
            )
            return Redirect(request.url_for("auth.team-policy"))
        except PendingLinkRequired as link_required:
            # Mint a PendingOidcLink row carrying the validated OIDC
            # identity. Cookie value is the plaintext token; row stores
            # the HMAC hash. See _link_confirm.py for the full flow.
            raw_token, _row = await mint_pending_oidc_link(
                db_session, exc=link_required
            )
            await db_session.commit()
            response = Redirect(
                request.url_for("link-confirm.show"),
                cookies=[issue_pending_link_cookie(raw_token)],
            )
            flash(
                request,
                "Sign in with your password to link your "
                f"{identity.provider.value.capitalize()} account.",
                category="info",
            )
            return response
        await db_session.commit()
        # Refresh after commit so the in-memory user reflects
        # activated_at / oauth_accounts / etc.
        await db_session.refresh(user)
        # Operator MFA posture (design — Control 1): operators must have
        # a passkey even when signing in via the corporate IdP.
        try:
            await assert_operator_mfa_posture(db_session, user)
        except OperatorPostureError:
            if not user.is_active:
                _stash_auth_flow(request, page="account_disabled", context={})
                return Redirect(request.url_for("auth.account-disabled"))
            # Bootstrap: without an in-app enrollment path, a fresh
            # operator with no passkey would dead-end. Establish a partial
            # session pinned to /settings/security/mfa/enroll — the
            # MfaEnrollmentTrap middleware keeps the operator there until
            # passkey_finish flips `requires_passkey_enrollment` off.
            # Mirrors the root account bootstrap in _access.py for the
            # OIDC path.
            request.session["user_id"] = user.email
            request.session["auth_method"] = identity.provider.value
            request.session["mfa_enrolled"] = False
            request.session["requires_passkey_enrollment"] = True
            flash(
                request,
                "Enroll a passkey to finish setting up your operator account.",
                category="info",
            )
            return Redirect(request.url_for("mfa.enroll.page"))
        await _maybe_claim_invitation(
            request, db_session, user_email=user.email, identity=identity
        )
        return await _establish_session(request, user, identity, redirect_to)


def _provider_config_or_raise(provider: Provider) -> ProviderConfig:
    """Return the runtime config for ``provider`` or refuse.

    Refuses with 401 if the credentials aren't configured (avoids
    leaking a half-mounted provider through the route).
    """
    configs = load_provider_configs()
    cfg = configs.get(provider)
    if cfg is None or not cfg.client_id or not cfg.client_secret:
        raise NotAuthorizedException(f"{provider.value} sign-in is not configured")
    return cfg


def _provider_available(provider_value: str) -> bool:
    """True if ``provider_value`` is a configured, sign-in-able provider.

    Used by the enforcement self-migration branch to decide whether the
    user can be bounced to the required provider, or must dead-end at
    ``/auth/team-policy``.
    """
    try:
        prov = Provider(provider_value)
    except ValueError:
        return False
    cfg = load_provider_configs().get(prov)
    return cfg is not None and bool(cfg.client_id) and bool(cfg.client_secret)


async def _route_identity_error(
    request: Request, exc: IdentityError, prov: Provider
) -> Redirect:
    """Route a structured ``IdentityError`` to the appropriate landing page."""
    if exc.reason == "no_verified_email":
        _stash_auth_flow(
            request,
            page="idp_config_required",
            context={"provider": prov.value},
        )
        return Redirect(request.url_for("auth.idp-config-required"))
    if exc.reason == "personal_account_blocked":
        _stash_auth_flow(
            request,
            page="invitation_required",
            context={"provider": prov.value, "reason": "personal_account"},
        )
        return Redirect(request.url_for("auth.invitation-required"))
    flash(
        request,
        "We couldn't verify your sign-in. Please try again.",
        category="error",
    )
    return Redirect(request.url_for("login"))


async def _establish_session(
    request: Request,
    user: User,
    identity: ExtractedIdentity,
    redirect_to: str,
) -> InertiaRedirect | Redirect:
    """Set the session keys and redirect to ``redirect_to``."""
    if not user.is_active:
        _stash_auth_flow(request, page="account_disabled", context={})
        return Redirect(request.url_for("auth.account-disabled"))

    request.session["user_id"] = user.email
    request.session["auth_method"] = identity.provider.value
    request.session["last_auth_at"] = datetime.now(UTC).isoformat()
    flash(
        request,
        "Your account was successfully authenticated.",
        category="info",
    )
    safe_target = safe_redirect_target(redirect_to)
    if safe_target == "/dashboard":
        return InertiaRedirect(request, request.url_for("dashboard"))
    return InertiaRedirect(request, safe_target)


async def _maybe_claim_invitation(
    request: Request,
    db_session: AsyncSession,
    *,
    user_email: str,
    identity: ExtractedIdentity,
) -> None:
    """Consume an in-flight invitation if one was staged in the session.

    Pops ``invitation_token_hash`` from the session (set by the
    ``/invitations/{token}/`` GET handler), looks the invitation up via
    the canonical helper, validates email + force_provider, and claims
    the row atomically. Silent on all failures — we treat invitation
    consumption as best-effort: if the row is missing, expired, or for
    a different user, the OIDC sign-in still succeeds; the invitation
    just won't be marked accepted. The team membership row was
    pre-created at provisioning time, so the user lands signed in with
    their team intact either way.

    Anti-enumeration: never surfaces a structured reason to the client;
    a stale or wrong-email invitation is treated identically to a
    well-formed claim.
    """
    token_hash = request.session.pop("invitation_token_hash", None)
    request.session.pop("invitation_token", None)
    if token_hash is None:
        return
    invitation = await find_invitation_by_token_hash(db_session, token_hash)
    if invitation is None:
        return
    if invitation.email.lower() != user_email.lower():
        return
    if (
        invitation.force_provider is not None
        and invitation.force_provider != identity.provider.value
    ):
        return
    try:
        assert_invitation_usable(invitation)
    except InvitationUnusableError:
        return
    claimed = await claim_invitation_accepted(db_session, invitation.id)
    if claimed:
        await db_session.commit()
