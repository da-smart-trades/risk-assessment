# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""MFA enrollment + verify controller — PR-3 flow.

Combines TOTP / WebAuthn-passkey enrollment in
``/settings/security/mfa/...`` with the password+MFA login verify in
``/auth/mfa/...``. The verify flow consumes server-side
``MfaAttempt`` rows (single-use, atomic) — the cookie carries only an
HMAC-keyed lookup token; never identity.

The legacy ``_mfa.py`` / ``_mfa_challenge.py`` controllers remain
registered until PR-4 prunes them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003

from litestar import Controller, Request, delete, get, post
from litestar.datastructures import Cookie
from litestar.di import Provide
from litestar.exceptions import NotAuthorizedException
from litestar.params import Parameter
from litestar.response import Response
from litestar_vite.inertia import InertiaRedirect, flash
from sqlalchemy import select
from sqlalchemy.orm import undefer_group

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.accounts.schemas import (
    MfaBackupCodes,
    MfaConfirm,
    MfaEnrollmentPage,
    MfaPasskeyAssertionOptions,
    MfaPasskeyRegisterBegin,
    MfaPasskeyRegisterFinish,
    MfaPasskeyRegisterOptions,
    MfaSetup,
    MfaVerifyPage,
    MfaVerifyPasskeyRequest,
    MfaVerifyRecoveryRequest,
    MfaVerifyTotpRequest,
)
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.lib.mfa.passkey import (
    PasskeyVerifyError,
    build_assertion_options,
    build_registration_options,
    verify_assertion,
    verify_registration,
)
from cert_ra.api.lib.mfa.recovery import issue_recovery_codes
from cert_ra.api.lib.mfa.runtime import expected_origin, rp_id, rp_name
from cert_ra.api.lib.mfa.totp import (
    generate_qr_data_url,
    generate_secret,
    verify_code,
)
from cert_ra.api.lib.mfa_attempts import (
    MfaAttemptUnusableError,
    assert_mfa_attempt_usable,
    claim_mfa_attempt_consumed,
    find_mfa_attempt_by_token_hash,
    mint_mfa_attempt,
)
from cert_ra.api.lib.operator_roles import user_is_operator
from cert_ra.api.lib.recovery_codes import claim_recovery_code_used
from cert_ra.api.lib.session_rotation import reauthenticate_session
from cert_ra.api.lib.token_hashing import hmac_sha256
from cert_ra.db.models import MfaAttempt, UserPasskey

if TYPE_CHECKING:
    from collections.abc import Callable

    from cert_ra.db.models import User as UserModel

__all__ = ("MfaEnrollmentController", "MfaVerifyController")

MFA_ATTEMPT_COOKIE = "mfa_attempt"
"""Cookie carrying the MFA attempt lookup token. Path-scoped to /auth/mfa
so it doesn't bleed into other traffic."""

MFA_COOKIE_PATH = "/auth/mfa"

_TOTP_PENDING_SESSION_KEY = "mfa_totp_pending_secret"
"""During TOTP enrollment we hold the unconfirmed secret in the session
between begin and confirm. Cleared on confirm (success or restart)."""

_PASSKEY_PENDING_SESSION_KEY = "mfa_passkey_pending_challenge"
"""During passkey enrollment we hold the WebAuthn challenge so the
browser's response can be verified against it. Cleared on verify."""


def _mfa_cookie(value: str, max_age: int) -> Cookie:
    """Build the MFA attempt cookie."""
    from cert_ra.settings.api import get_app_settings

    settings = get_app_settings()
    return Cookie(
        key=MFA_ATTEMPT_COOKIE,
        value=value,
        path=MFA_COOKIE_PATH,
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,  # type: ignore[arg-type]
    )


def _clear_mfa_cookie() -> Cookie:
    """Clear the MFA attempt cookie."""
    return Cookie(
        key=MFA_ATTEMPT_COOKIE,
        value="",
        path=MFA_COOKIE_PATH,
        max_age=0,
        httponly=True,
    )


async def issue_mfa_attempt_cookie(
    db: object, *, user_id: UUID, with_webauthn_challenge: bool
) -> tuple[Cookie, bytes | None]:
    """Mint an ``MfaAttempt`` and the cookie that locates it.

    Used by the login handler after successful password verification.

    Args:
        db: Async session.
        user_id: The user who passed the password step.
        with_webauthn_challenge: True if the user has a passkey enrolled
            (we pre-mint the WebAuthn challenge so it's bound to this
            attempt's lifetime).

    Returns:
        Tuple of (cookie carrying the lookup token, optional challenge
        bytes if minted). The caller commits.
    """
    from cert_ra.api.lib.mfa_attempts import MFA_ATTEMPT_TTL

    challenge: bytes | None = None
    if with_webauthn_challenge:
        # Defer the real challenge generation to the GET-options call —
        # we don't need one for TOTP-only verifies. The row's
        # webauthn_challenge stays NULL until /auth/mfa/passkey/options
        # is hit and binds a freshly-built one.
        challenge = None
    plain_token, _row = await mint_mfa_attempt(
        db,  # type: ignore[arg-type]
        user_id=user_id,
        webauthn_challenge=challenge,
    )
    return _mfa_cookie(plain_token, int(MFA_ATTEMPT_TTL.total_seconds())), challenge


class MfaEnrollmentController(Controller):
    """MFA enrollment for password users.

    Mounted at ``/settings/security/mfa``. The enrollment-trap
    middleware funnels first-time password users here.
    """

    path = "/settings/security/mfa"
    include_in_schema = False
    dependencies = {"users_service": Provide(provide_users_service)}  # noqa: RUF012
    signature_namespace = {  # noqa: RUF012
        "UserService": UserService,
        "MfaConfirm": MfaConfirm,
        "MfaPasskeyRegisterBegin": MfaPasskeyRegisterBegin,
        "MfaPasskeyRegisterFinish": MfaPasskeyRegisterFinish,
    }
    cache = False
    guards = [requires_active_user]  # noqa: RUF012

    @get(
        component="settings/security/mfa-enroll",
        name="mfa.enroll.page",
        path="/enroll",
    )
    async def enroll_page(
        self,
        current_user: UserModel,
        users_service: UserService,
    ) -> MfaEnrollmentPage:
        """Render the enrollment page with current factor state."""
        user = await users_service.get_one_or_none(
            id=current_user.id, load=[undefer_group("security_sensitive")]
        )
        if user is None:
            raise NotAuthorizedException("Account not found")
        passkey_count = await _count_passkeys(users_service, user.id)
        return MfaEnrollmentPage(
            has_totp=user.is_two_factor_enabled and bool(user.totp_secret),
            has_passkey=passkey_count > 0,
            factor_count=(1 if user.is_two_factor_enabled else 0) + passkey_count,
            enroll_complete=user.is_two_factor_enabled or passkey_count > 0,
        )

    @post(path="/totp/begin", name="mfa.enroll.totp.begin")
    async def totp_begin(
        self,
        request: Request,
        current_user: UserModel,
    ) -> MfaSetup:
        """Generate a TOTP secret + QR code; hold the secret in session.

        The secret is NOT persisted until the user confirms with a
        correct code (mirrors the legacy flow's atomic semantics).
        """
        secret = generate_secret()
        request.session[_TOTP_PENDING_SESSION_KEY] = secret
        qr = generate_qr_data_url(secret, current_user.email, rp_name())
        return MfaSetup(secret=secret, qr_code=qr)

    @post(path="/totp/confirm", name="mfa.enroll.totp.confirm")
    async def totp_confirm(
        self,
        request: Request,
        current_user: UserModel,
        users_service: UserService,
        data: MfaConfirm,
    ) -> MfaBackupCodes:
        """Persist the TOTP secret + issue recovery codes."""
        pending = request.session.get(_TOTP_PENDING_SESSION_KEY)
        if not pending or not verify_code(pending, data.code):
            request.session.pop(_TOTP_PENDING_SESSION_KEY, None)
            flash(request, "Invalid code. Try again.", category="error")
            raise NotAuthorizedException("Invalid TOTP code")
        await users_service.update(
            item_id=current_user.id,
            data={
                "totp_secret": pending,
                "is_two_factor_enabled": True,
                "two_factor_confirmed_at": datetime.now(UTC),
            },
            load=[undefer_group("security_sensitive")],
        )
        db = users_service.repository.session
        codes = await issue_recovery_codes(db, current_user.id)
        await db.commit()
        request.session.pop(_TOTP_PENDING_SESSION_KEY, None)
        request.session["mfa_enrolled"] = True
        return MfaBackupCodes(codes=codes)

    @post(path="/passkey/begin", name="mfa.enroll.passkey.begin")
    async def passkey_begin(
        self,
        request: Request,
        current_user: UserModel,
        users_service: UserService,
        data: MfaPasskeyRegisterBegin,  # noqa: ARG002 — accepted for symmetry
    ) -> MfaPasskeyRegisterOptions:
        """Build a WebAuthn registration challenge for ``current_user``."""
        existing = await _list_credential_ids(users_service, current_user.id)
        challenge = build_registration_options(
            rp_id=rp_id(),
            rp_name=rp_name(),
            user_id=current_user.id.bytes,
            user_name=current_user.email,
            user_display_name=current_user.name or current_user.email,
            existing_credential_ids=existing,
        )
        request.session[_PASSKEY_PENDING_SESSION_KEY] = list(challenge.challenge)
        return MfaPasskeyRegisterOptions(options_json=challenge.options_json)

    @post(path="/passkey/finish", name="mfa.enroll.passkey.finish")
    async def passkey_finish(
        self,
        request: Request,
        current_user: UserModel,
        users_service: UserService,
        data: MfaPasskeyRegisterFinish,
    ) -> MfaBackupCodes:
        """Verify the browser's registration response + insert a UserPasskey."""
        pending = request.session.pop(_PASSKEY_PENDING_SESSION_KEY, None)
        if not pending:
            raise NotAuthorizedException("Passkey enrollment expired")
        try:
            verified = verify_registration(
                response_json=data.response_json,
                expected_challenge=bytes(pending),
                rp_id=rp_id(),
                origin=expected_origin(),
            )
        except PasskeyVerifyError as exc:
            raise NotAuthorizedException("Passkey rejected") from exc
        db = users_service.repository.session
        # Recovery codes are issued once — only on first factor enrollment.
        # This MUST be checked before adding the passkey below: otherwise the
        # pending UserPasskey autoflushes when ``_has_any_factor`` queries the
        # passkey count, so it counts the credential being enrolled and reports
        # "not first factor" — suppressing recovery codes on first-passkey setup.
        first_factor = not await _has_any_factor(users_service, current_user.id)
        db.add(
            UserPasskey(
                user_id=current_user.id,
                credential_id=verified.credential_id,
                public_key=verified.public_key,
                sign_count=verified.sign_count,
                aaguid=verified.aaguid,
                device_name=data.device_name[:128] or "Passkey",
            )
        )
        codes: list[str] = []
        if first_factor:
            codes = await issue_recovery_codes(db, current_user.id)
        await db.commit()
        request.session["mfa_enrolled"] = True
        # Release the root-account bootstrap trap (a no-op for everyone
        # else). The flag is only ever set by the root password-login
        # branch in _access.py.
        request.session.pop("requires_passkey_enrollment", None)
        return MfaBackupCodes(codes=codes)

    @delete(
        path="/passkey/{passkey_id:uuid}", name="mfa.passkey.remove", status_code=303
    )
    async def passkey_remove(
        self,
        request: Request,
        current_user: UserModel,
        users_service: UserService,
        passkey_id: Annotated[
            UUID, Parameter(title="Passkey ID", description="The passkey row id.")
        ],
    ) -> InertiaRedirect:
        """Remove a passkey from the user's account."""
        db = users_service.repository.session
        row = (
            await db.scalars(
                select(UserPasskey).where(
                    UserPasskey.id == passkey_id,
                    UserPasskey.user_id == current_user.id,
                )
            )
        ).first()
        if row is not None:
            await db.delete(row)
            await db.commit()
            flash(request, "Passkey removed.", category="info")
        return InertiaRedirect(request, request.url_for("mfa.enroll.page"))


class MfaVerifyController(Controller):
    """MFA verify during login.

    Mounted at ``/auth/mfa``. Reads the ``mfa_attempt`` cookie, locates
    the server-side row via the canonical helper, and atomically
    consumes the attempt on each verify endpoint.
    """

    path = "/auth/mfa"
    include_in_schema = False
    dependencies = {"users_service": Provide(provide_users_service)}  # noqa: RUF012
    signature_namespace = {  # noqa: RUF012
        "UserService": UserService,
        "MfaVerifyTotpRequest": MfaVerifyTotpRequest,
        "MfaVerifyRecoveryRequest": MfaVerifyRecoveryRequest,
        "MfaVerifyPasskeyRequest": MfaVerifyPasskeyRequest,
    }
    cache = False
    exclude_from_auth = True

    @get(component="auth/mfa", name="mfa.verify.page", path="/")
    async def verify_page(
        self,
        request: Request,
        users_service: UserService,
    ) -> MfaVerifyPage | InertiaRedirect:
        """Render the verify-prompt page or bounce to login if no attempt."""
        attempt, _ = await _resolve_attempt(request, users_service)
        if attempt is None or attempt.user_id is None:
            return InertiaRedirect(request, request.url_for("login"))
        user = await users_service.get_one_or_none(
            id=attempt.user_id, load=[undefer_group("security_sensitive")]
        )
        if user is None:
            return InertiaRedirect(request, request.url_for("login"))
        passkey_count = await _count_passkeys(users_service, attempt.user_id)
        return MfaVerifyPage(
            has_totp=user.is_two_factor_enabled and bool(user.totp_secret),
            has_passkey=passkey_count > 0,
            has_recovery=True,
        )

    @post(path="/totp", name="mfa.verify.totp")
    async def verify_totp(
        self,
        request: Request,
        users_service: UserService,
        data: MfaVerifyTotpRequest,
    ) -> InertiaRedirect:
        """Verify a TOTP code, claim the attempt, establish the session.

        Operators are refused here — they must use a passkey (Control 1).
        """
        return await _finalize_or_reject(
            request,
            users_service,
            verifier=lambda user: bool(
                user.totp_secret and verify_code(user.totp_secret, data.code)
            ),
            forbid_operator=True,
        )

    @post(path="/recovery", name="mfa.verify.recovery")
    async def verify_recovery(
        self,
        request: Request,
        users_service: UserService,
        data: MfaVerifyRecoveryRequest,
    ) -> InertiaRedirect:
        """Verify a recovery code; claim it and the attempt atomically."""
        attempt, _ = await _resolve_attempt(request, users_service)
        if attempt is None or attempt.user_id is None:
            return InertiaRedirect(request, request.url_for("login"))
        db = users_service.repository.session
        ok = await claim_recovery_code_used(db, attempt.user_id, data.code.strip())
        consumed = await claim_mfa_attempt_consumed(
            db, attempt.id, outcome="success" if ok else "fail"
        )
        if not (ok and consumed):
            await db.commit()
            flash(request, "Invalid recovery code.", category="error")
            return InertiaRedirect(request, request.url_for("mfa.verify.page"))
        return await _establish_mfa_session(request, users_service, attempt.user_id)

    @post(path="/passkey/options", name="mfa.verify.passkey.options")
    async def verify_passkey_options(
        self,
        request: Request,
        users_service: UserService,
    ) -> MfaPasskeyAssertionOptions:
        """Build a WebAuthn assertion challenge and pin it to the attempt."""
        attempt, _ = await _resolve_attempt(request, users_service)
        if attempt is None or attempt.user_id is None:
            raise NotAuthorizedException("No active MFA attempt")
        cred_ids = await _list_credential_ids(users_service, attempt.user_id)
        challenge = build_assertion_options(
            rp_id=rp_id(), allowed_credential_ids=cred_ids
        )
        db = users_service.repository.session
        attempt.webauthn_challenge = challenge.challenge
        await db.commit()
        return MfaPasskeyAssertionOptions(options_json=challenge.options_json)

    @post(path="/passkey", name="mfa.verify.passkey")
    async def verify_passkey(
        self,
        request: Request,
        users_service: UserService,
        data: MfaVerifyPasskeyRequest,
    ) -> InertiaRedirect:
        """Verify a WebAuthn assertion; bump counter; claim the attempt."""
        attempt, _ = await _resolve_attempt(request, users_service)
        if attempt is None or attempt.user_id is None or not attempt.webauthn_challenge:
            return InertiaRedirect(request, request.url_for("login"))
        # Locate the credential the browser claims to be using.
        response = json.loads(data.response_json)
        cred_id_b64 = response.get("id", "")
        from webauthn.helpers import base64url_to_bytes

        try:
            cred_id = base64url_to_bytes(cred_id_b64)
        except Exception as exc:
            raise NotAuthorizedException("Malformed passkey") from exc
        db = users_service.repository.session
        passkey = (
            await db.scalars(
                select(UserPasskey).where(
                    UserPasskey.user_id == attempt.user_id,
                    UserPasskey.credential_id == cred_id,
                )
            )
        ).first()
        if passkey is None:
            await claim_mfa_attempt_consumed(db, attempt.id, outcome="fail")
            await db.commit()
            return InertiaRedirect(request, request.url_for("mfa.verify.page"))
        try:
            verified = verify_assertion(
                response_json=data.response_json,
                expected_challenge=bytes(attempt.webauthn_challenge),
                rp_id=rp_id(),
                origin=expected_origin(),
                credential_public_key=bytes(passkey.public_key),
                credential_current_sign_count=passkey.sign_count,
            )
        except PasskeyVerifyError:
            await claim_mfa_attempt_consumed(db, attempt.id, outcome="fail")
            await db.commit()
            flash(request, "Passkey rejected.", category="error")
            return InertiaRedirect(request, request.url_for("mfa.verify.page"))
        consumed = await claim_mfa_attempt_consumed(db, attempt.id, outcome="success")
        if not consumed:
            await db.commit()
            return InertiaRedirect(request, request.url_for("login"))
        passkey.sign_count = verified.new_sign_count
        passkey.last_used_at = datetime.now(UTC)
        await db.commit()
        return await _establish_mfa_session(request, users_service, attempt.user_id)


async def _resolve_attempt(
    request: Request,
    users_service: UserService,
) -> tuple[MfaAttempt | None, str | None]:
    """Read the cookie, locate the row, run the canonical assertions.

    Returns ``(attempt, token)`` on success, ``(None, None)`` otherwise.
    Anti-enumeration: all failure modes return the same shape.
    """
    token = request.cookies.get(MFA_ATTEMPT_COOKIE)
    if not token:
        return None, None
    db = users_service.repository.session
    attempt = await find_mfa_attempt_by_token_hash(db, hmac_sha256(token))
    try:
        assert_mfa_attempt_usable(attempt)
    except MfaAttemptUnusableError:
        return None, None
    return attempt, token


async def _finalize_or_reject(
    request: Request,
    users_service: UserService,
    *,
    verifier: Callable[[UserModel], bool],
    forbid_operator: bool = False,
) -> InertiaRedirect:
    """Shared TOTP/recovery success path: verify, claim, establish.

    When ``forbid_operator`` is set, an operator-team member is refused
    regardless of code validity — operators must use a passkey, not TOTP
    (design — Control 1).
    """
    attempt, _ = await _resolve_attempt(request, users_service)
    if attempt is None or attempt.user_id is None:
        return InertiaRedirect(request, request.url_for("login"))
    user = await users_service.get_one_or_none(
        id=attempt.user_id,
        load=[undefer_group("security_sensitive")],
    )
    if user is None:
        return InertiaRedirect(request, request.url_for("login"))
    db = users_service.repository.session
    if forbid_operator and await user_is_operator(db, user):
        # Burn the attempt so a refused operator can't keep retrying TOTP.
        await claim_mfa_attempt_consumed(db, attempt.id, outcome="fail")
        await db.commit()
        flash(
            request,
            "Operators must verify with a passkey, not an authenticator code.",
            category="error",
        )
        return InertiaRedirect(request, request.url_for("mfa.verify.page"))
    ok = verifier(user)
    consumed = await claim_mfa_attempt_consumed(
        db,
        attempt.id,
        outcome="success" if ok else "fail",
    )
    if not (ok and consumed):
        await db.commit()
        flash(request, "Invalid code.", category="error")
        return InertiaRedirect(request, request.url_for("mfa.verify.page"))
    return await _establish_mfa_session(
        request,
        users_service,
        attempt.user_id,
    )


async def _establish_mfa_session(
    request: Request,
    users_service: UserService,
    user_id: UUID,
) -> InertiaRedirect:
    """Rotate the session, set the auth keys, redirect to the dashboard."""
    user = await users_service.get_one_or_none(id=user_id)
    if user is None:
        raise NotAuthorizedException("User vanished")
    db = users_service.repository.session
    await reauthenticate_session(request, db, user_email=user.email)
    request.session["user_id"] = user.email
    request.session["auth_method"] = "password"
    request.session["last_auth_at"] = datetime.now(UTC).isoformat()
    request.session["mfa_enrolled"] = True
    response = InertiaRedirect(request, request.url_for("dashboard"))
    response.cookies.append(_clear_mfa_cookie())
    flash(request, "Your account was successfully authenticated.", category="info")
    return response


async def _list_credential_ids(
    users_service: UserService, user_id: UUID
) -> list[bytes]:
    """Return the user's enrolled credential_ids."""
    db = users_service.repository.session
    rows = await db.scalars(
        select(UserPasskey.credential_id).where(UserPasskey.user_id == user_id)
    )
    return [bytes(r) for r in rows.all()]


async def _count_passkeys(users_service: UserService, user_id: UUID) -> int:
    """Count the user's enrolled passkeys."""
    db = users_service.repository.session
    rows = await db.scalars(
        select(UserPasskey.id).where(UserPasskey.user_id == user_id)
    )
    return len(rows.all())


async def _has_any_factor(users_service: UserService, user_id: UUID) -> bool:
    """True if the user has any MFA factor (TOTP or passkey)."""
    user = await users_service.get_one_or_none(id=user_id)
    if user is not None and user.is_two_factor_enabled:
        return True
    return await _count_passkeys(users_service, user_id) > 0


# Re-export Response so the module is callable in core.py without
# the cyclic-import gymnastics.
__all_response_ = Response
