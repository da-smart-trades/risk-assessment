# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""WebAuthn / passkey enrollment + assertion wrapper around py_webauthn.

Wraps the four library calls the controller uses
(``generate_registration_options``, ``verify_registration_response``,
``generate_authentication_options``, ``verify_authentication_response``)
behind a smaller, project-local surface.

Sign-counter rule (design checklist #20): platform authenticators
(Touch ID, Face ID, Windows Hello) return a counter of 0 indefinitely.
The verifier MUST accept ``new == 0 == stored`` without enforcing
monotonicity. For incrementing authenticators (YubiKey class),
``new <= stored`` MUST raise ``SignCountRegressionError``.
"""

from __future__ import annotations

from dataclasses import dataclass

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialType,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)


class PasskeyVerifyError(Exception):
    """Raised when an enrollment or assertion fails verification."""


class SignCountRegressionError(PasskeyVerifyError):
    """Raised when an incrementing authenticator's counter regresses.

    Carries no caller-visible detail (anti-enumeration); the controller
    renders a generic ``passkey rejected`` page.
    """


@dataclass(frozen=True, slots=True)
class RegistrationChallenge:
    """The data the page needs to start an enrollment ceremony.

    ``options_json`` is the WebAuthn options object serialized for the
    browser; ``challenge`` is the raw bytes we pin to the MfaAttempt
    row for replay defense.
    """

    options_json: str
    challenge: bytes


@dataclass(frozen=True, slots=True)
class VerifiedRegistration:
    """Result of a successful enrollment verification."""

    credential_id: bytes
    public_key: bytes
    sign_count: int
    aaguid: str | None


@dataclass(frozen=True, slots=True)
class AssertionChallenge:
    """The data the page needs to start an assertion ceremony."""

    options_json: str
    challenge: bytes


@dataclass(frozen=True, slots=True)
class VerifiedAssertion:
    """Result of a successful assertion verification."""

    credential_id: bytes
    new_sign_count: int


def build_registration_options(
    *,
    rp_id: str,
    rp_name: str,
    user_id: bytes,
    user_name: str,
    user_display_name: str,
    existing_credential_ids: list[bytes],
) -> RegistrationChallenge:
    """Build the WebAuthn registration challenge.

    Args:
        rp_id: The relying-party identifier — the bare apex hostname
            (``app.certora.com``, not ``https://app.certora.com``).
            Authenticators bind credentials to this value.
        rp_name: Human-readable RP name shown in the authenticator UI.
        user_id: Stable 16-byte user-handle. We pass ``UUID.bytes``.
        user_name: Login identifier (typically the email).
        user_display_name: Display name shown by the authenticator.
        existing_credential_ids: Already-registered passkeys for this
            user. Sent as ``excludeCredentials`` so the authenticator
            refuses to register the same credential twice.
    """
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=user_id,
        user_name=user_name,
        user_display_name=user_display_name,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(
                id=cred_id, type=PublicKeyCredentialType.PUBLIC_KEY
            )
            for cred_id in existing_credential_ids
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    return RegistrationChallenge(
        options_json=_options_to_json(options),
        challenge=options.challenge,
    )


def verify_registration(
    *,
    response_json: str,
    expected_challenge: bytes,
    rp_id: str,
    origin: str,
) -> VerifiedRegistration:
    """Verify the browser's registration response.

    Raises:
        PasskeyVerifyError: On any verification failure. The original
            exception is dropped — callers render a generic error.
    """
    try:
        verified = verify_registration_response(
            credential=response_json,
            expected_challenge=expected_challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
        )
    except Exception as exc:
        msg = "passkey registration failed"
        raise PasskeyVerifyError(msg) from exc
    return VerifiedRegistration(
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        aaguid=str(verified.aaguid) if verified.aaguid else None,
    )


def build_assertion_options(
    *,
    rp_id: str,
    allowed_credential_ids: list[bytes],
) -> AssertionChallenge:
    """Build the WebAuthn assertion challenge.

    ``allowed_credential_ids`` is empty for the passwordless flow
    (discoverable credentials) and non-empty for the password+MFA
    flow (we know which user is signing in).
    """
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(
                id=cred_id, type=PublicKeyCredentialType.PUBLIC_KEY
            )
            for cred_id in allowed_credential_ids
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return AssertionChallenge(
        options_json=_options_to_json(options),
        challenge=options.challenge,
    )


def verify_assertion(
    *,
    response_json: str,
    expected_challenge: bytes,
    rp_id: str,
    origin: str,
    credential_public_key: bytes,
    credential_current_sign_count: int,
) -> VerifiedAssertion:
    """Verify the browser's assertion response.

    Enforces the sign-counter rule: incrementing authenticators must
    monotonically increase; platform authenticators stuck at 0 are
    accepted indefinitely.

    Raises:
        PasskeyVerifyError: On any verification failure.
        SignCountRegressionError: On counter regression.
    """
    try:
        verified = verify_authentication_response(
            credential=response_json,
            expected_challenge=expected_challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=credential_public_key,
            credential_current_sign_count=credential_current_sign_count,
        )
    except Exception as exc:
        msg = "passkey assertion failed"
        raise PasskeyVerifyError(msg) from exc
    new = int(verified.new_sign_count)
    # design #20: 0 == 0 is fine forever (platform authenticators).
    # Any incrementing authenticator must strictly increase.
    if (
        credential_current_sign_count != 0 or new != 0
    ) and new <= credential_current_sign_count:
        msg = "sign-count regression"
        raise SignCountRegressionError(msg)
    return VerifiedAssertion(
        credential_id=verified.credential_id,
        new_sign_count=new,
    )


def _options_to_json(options: object) -> str:
    """Serialize a webauthn options object to JSON for the browser.

    py_webauthn's ``options_to_json`` lives at
    ``webauthn.helpers.options_to_json`` but the import path moved
    across versions; we wrap it once so the rest of the code is stable.
    """
    from webauthn.helpers import options_to_json

    return options_to_json(options)  # type: ignore[arg-type]
