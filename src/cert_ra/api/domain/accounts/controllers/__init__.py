# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""User Account Controllers."""

from ._access import AccessController
from ._email import EmailVerificationController
from ._force_password_change import ForcePasswordChangeController
from ._landing import LandingController
from ._link_confirm import LinkConfirmController
from ._mfa import MfaController
from ._mfa_challenge import MfaChallengeController
from ._mfa_v2 import MfaEnrollmentController, MfaVerifyController
from ._oauth_accounts import OAuthAccountController
from ._oidc import OidcController
from ._password import PasswordResetController
from ._password_reset_v2 import PasswordResetV2Controller
from ._profile import ProfileController
from ._reauth import ReauthController
from ._roles import RoleController, UserRoleController
from ._unlock import UnlockController
from ._users import UserController
from .enforcement_migration import ProviderSwitchController

__all__ = (
    "AccessController",
    "EmailVerificationController",
    "ForcePasswordChangeController",
    "LandingController",
    "LinkConfirmController",
    "MfaChallengeController",
    "MfaController",
    "MfaEnrollmentController",
    "MfaVerifyController",
    "OAuthAccountController",
    "OidcController",
    "PasswordResetController",
    "PasswordResetV2Controller",
    "ProfileController",
    "ProviderSwitchController",
    "ReauthController",
    "RoleController",
    "UnlockController",
    "UserController",
    "UserRoleController",
)
