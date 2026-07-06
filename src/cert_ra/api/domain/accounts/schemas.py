# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

import msgspec

from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.db.models.team_roles import TeamRoles

__all__ = (
    "AccountLogin",
    "AccountRegister",
    "EmailSent",
    "ForgotPasswordRequest",
    "MfaBackupCodes",
    "MfaChallenge",
    "MfaConfirm",
    "MfaDisable",
    "MfaEnrollmentPage",
    "MfaFactorListItem",
    "MfaPasskeyAssertionOptions",
    "MfaPasskeyRegisterBegin",
    "MfaPasskeyRegisterFinish",
    "MfaPasskeyRegisterOptions",
    "MfaSetup",
    "MfaVerifyPage",
    "MfaVerifyPasskeyRequest",
    "MfaVerifyRecoveryRequest",
    "MfaVerifyTotpRequest",
    "PasswordReset",
    "PasswordResetToken",
    "PasswordUpdate",
    "PasswordVerify",
    "ProfileUpdate",
    "User",
    "UserCreate",
    "UserRole",
    "UserRoleAdd",
    "UserRoleRevoke",
    "UserTeam",
    "UserUpdate",
)


class UserTeam(CamelizedBaseStruct):
    """Holds team details for a user.

    This is nested in the User Model for 'team'
    """

    team_id: UUID
    team_name: str
    team_slug: str
    is_owner: bool = False
    role: TeamRoles = TeamRoles.MEMBER


class UserRole(CamelizedBaseStruct):
    """Holds role details for a user.

    This is nested in the User Model for 'roles'
    """

    role_id: UUID
    role_slug: str
    role_name: str
    assigned_at: datetime


class OauthAccount(CamelizedBaseStruct):
    """Holds linked OAuth details for a user.

    Note: Sensitive fields (access_token, refresh_token, expires_at) are
    intentionally excluded from this schema to prevent exposure to the frontend.
    """

    id: UUID
    oauth_name: str
    account_id: str
    account_email: str
    scopes: list[str] | None = None


class User(CamelizedBaseStruct):
    """User properties to use for a response."""

    id: UUID
    email: str
    name: str | None = None
    is_superuser: bool = False
    is_active: bool = False
    is_verified: bool = False
    has_password: bool = False
    is_two_factor_enabled: bool = False
    is_operator_editor: bool = False
    is_operator_member: bool = False
    is_any_team_editor: bool = False
    teams: list[UserTeam] = msgspec.field(default_factory=list)
    roles: list[UserRole] = msgspec.field(default_factory=list)
    oauth_accounts: list[OauthAccount] = msgspec.field(default_factory=list)
    avatar_url: str | None = None


class UserCreate(CamelizedBaseStruct):
    email: str
    password: str
    name: str | None = None
    is_superuser: bool = False
    is_active: bool = True
    is_verified: bool = False


class UserUpdate(CamelizedBaseStruct, omit_defaults=True):
    email: str | None | msgspec.UnsetType = msgspec.UNSET
    password: str | None | msgspec.UnsetType = msgspec.UNSET
    name: str | None | msgspec.UnsetType = msgspec.UNSET
    is_superuser: bool | None | msgspec.UnsetType = msgspec.UNSET
    is_active: bool | None | msgspec.UnsetType = msgspec.UNSET
    is_verified: bool | None | msgspec.UnsetType = msgspec.UNSET


class AccountLogin(CamelizedBaseStruct):
    username: str
    password: str


class PasswordUpdate(CamelizedBaseStruct):
    current_password: str
    new_password: str


class PasswordVerify(CamelizedBaseStruct):
    current_password: str


class ProfileUpdate(CamelizedBaseStruct, omit_defaults=True):
    name: str | None | msgspec.UnsetType = msgspec.UNSET


class AccountRegister(CamelizedBaseStruct):
    email: str
    password: str
    name: str | None = None


class UserRoleAdd(CamelizedBaseStruct):
    """User role add ."""

    user_name: str


class UserRoleRevoke(CamelizedBaseStruct):
    """User role revoke ."""

    user_name: str


class ForgotPasswordRequest(CamelizedBaseStruct):
    """Request to send a password reset email."""

    email: str


class PasswordReset(CamelizedBaseStruct):
    """Reset password with token."""

    token: str
    password: str


class EmailSent(CamelizedBaseStruct):
    """Confirmation that an email was sent."""

    email_sent: bool = True


class PasswordResetToken(CamelizedBaseStruct):
    """Token data for password reset form."""

    token: str
    email: str


class MfaSetup(CamelizedBaseStruct):
    """Response with QR code and secret for MFA setup."""

    secret: str
    qr_code: str  # Base64 encoded PNG


class MfaConfirm(CamelizedBaseStruct):
    """Request to confirm MFA setup with a TOTP code."""

    code: str


class MfaChallenge(CamelizedBaseStruct):
    """Request to verify MFA during login."""

    code: str | None = None
    recovery_code: str | None = None


class MfaDisable(CamelizedBaseStruct):
    """Request to disable MFA with password confirmation."""

    password: str


class MfaBackupCodes(CamelizedBaseStruct):
    """Response with backup codes for MFA recovery."""

    codes: list[str]


class MfaPasskeyRegisterBegin(CamelizedBaseStruct):
    """Request body for the registration-options endpoint.

    The user provides a device label up-front so we can persist it
    alongside the credential on verify.
    """

    device_name: str


class MfaPasskeyRegisterOptions(CamelizedBaseStruct):
    """Response carrying the WebAuthn registration challenge."""

    options_json: str


class MfaPasskeyRegisterFinish(CamelizedBaseStruct):
    """Request body for the registration-verify endpoint."""

    device_name: str
    response_json: str


class MfaFactorListItem(CamelizedBaseStruct):
    """One enrolled factor (TOTP or passkey)."""

    id: str
    kind: str
    label: str | None = None
    created_at: str | None = None
    last_used_at: str | None = None


class MfaEnrollmentPage(CamelizedBaseStruct):
    """Props for the enrollment Inertia page."""

    has_totp: bool
    has_passkey: bool
    factor_count: int
    enroll_complete: bool


class MfaVerifyPage(CamelizedBaseStruct):
    """Props for the MFA verify Inertia prompt page."""

    has_totp: bool
    has_passkey: bool
    has_recovery: bool


class MfaVerifyTotpRequest(CamelizedBaseStruct):
    """Verify a TOTP code mid-login."""

    code: str


class MfaVerifyRecoveryRequest(CamelizedBaseStruct):
    """Verify a recovery code mid-login."""

    code: str


class MfaVerifyPasskeyRequest(CamelizedBaseStruct):
    """Verify a WebAuthn assertion mid-login."""

    response_json: str


class MfaPasskeyAssertionOptions(CamelizedBaseStruct):
    """WebAuthn assertion challenge for the verify endpoint."""

    options_json: str


class PasswordConfirm(CamelizedBaseStruct):
    """Request to confirm password before sensitive actions."""

    password: str
