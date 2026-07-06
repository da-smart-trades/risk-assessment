# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""HMAC-SHA-256 token hashing for the OIDC SSO state-row pattern.

All high-entropy single-use tokens (invitations, pending OIDC links,
MFA attempts, password reset, unlock, provider switch) are stored as
HMAC-SHA-256 hashes keyed on ``app_settings.secret_key``.

SHA-256 (not argon2id) because the input is high-entropy
(32+ random bytes from ``secrets.token_urlsafe(32)``); brute-force
resistance from a slow hash isn't useful and would break deterministic
equality lookup. HMAC keying defeats rainbow-table reuse if the DB
ever leaks.
"""

from __future__ import annotations

import hashlib
import hmac

from cert_ra.settings.api import get_app_settings


def hmac_sha256(plaintext: str) -> str:
    """Return the HMAC-SHA-256 hex digest of ``plaintext``.

    Deterministic: ``hmac_sha256(x) == hmac_sha256(x)`` always. Keyed on
    the app's signing secret so the hash can't be rainbow-table-attacked
    if the DB leaks.

    Args:
        plaintext: The raw token string (any length).

    Returns:
        Lowercase hex string, 64 characters long.
    """
    key = get_app_settings().secret_key.encode("utf-8")
    return hmac.new(key, plaintext.encode("utf-8"), hashlib.sha256).hexdigest()
