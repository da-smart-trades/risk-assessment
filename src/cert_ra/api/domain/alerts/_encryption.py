# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Symmetric encryption helpers for sensitive fields inside JSONB columns.

Used to encrypt-at-rest the ``secret`` field of ``WebhookIntegrationConfig``
before it is persisted into ``alert_integration.config`` JSONB. Column-level
encryption (``advanced_alchemy.types.EncryptedString``) doesn't apply here
because the field lives inside a JSONB blob, not in its own column.

The Fernet key is derived deterministically from ``app_settings.secret_key``
via SHA-256, so a re-keying of the app affects all encrypted material
consistently. SHA-256 always produces 32 bytes, satisfying Fernet's key-size
requirement regardless of the secret's length.
"""

from __future__ import annotations

import base64
import hashlib
from functools import cache

from cryptography.fernet import Fernet

from cert_ra.settings.api import get_app_settings

__all__ = (
    "ENCRYPTED_PREFIX",
    "decrypt_secret",
    "encrypt_secret",
    "is_encrypted",
)


ENCRYPTED_PREFIX = "enc:"
"""Marker that distinguishes ciphertext from a plaintext secret in storage.

Plaintext secrets should never reach the DB, but the prefix makes accidental
double-encryption visible and lets us migrate any unprefixed legacy values
in-place without ambiguity.
"""


@cache
def _fernet() -> Fernet:
    """Build a Fernet from the app secret key. Cached for the process lifetime."""
    digest = hashlib.sha256(get_app_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    """Encrypt a plaintext secret to a marker-prefixed ciphertext string.

    Idempotent: passing an already-encrypted value through ``encrypt_secret``
    returns it unchanged. This lets the service layer call ``encrypt_secret``
    on every write without having to track which fields are already encrypted.

    Args:
        plain: The plaintext secret (or a previously-produced ciphertext).

    Returns:
        ``ENCRYPTED_PREFIX`` + base64-encoded Fernet ciphertext.
    """
    if is_encrypted(plain):
        return plain
    token = _fernet().encrypt(plain.encode()).decode()
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_secret(stored: str) -> str:
    """Decrypt a marker-prefixed ciphertext back to plaintext.

    Args:
        stored: A value previously produced by ``encrypt_secret``.

    Returns:
        The plaintext secret.

    Raises:
        InvalidToken: If the ciphertext is corrupt or signed with a different key.
        ValueError: If ``stored`` is missing the encryption marker.
    """
    if not is_encrypted(stored):
        msg = "Value is missing the encryption marker; refusing to decrypt."
        raise ValueError(msg)
    token = stored.removeprefix(ENCRYPTED_PREFIX)
    return _fernet().decrypt(token.encode()).decode()


def is_encrypted(value: str) -> bool:
    """Return True if ``value`` carries the encryption marker."""
    return value.startswith(ENCRYPTED_PREFIX)
