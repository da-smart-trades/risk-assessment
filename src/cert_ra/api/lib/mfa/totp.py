# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""TOTP wrapper around pyotp with constant-time verification.

The wrapper exists for two reasons:

1. ``pyotp.TOTP.verify`` does an internal constant-time compare against
   the *current* window, but its handling of skew is window-by-window —
   we centralise the convention here so the controller never has to
   pick a skew.
2. The OIDC SSO design (#11) wants TOTP verification routed through a
   single helper so the canonical helper lint can reason about it.
"""

from __future__ import annotations

import base64
import io

import pyotp
import qrcode

# Allow ±1 30-second window (i.e. ±30s clock skew) — the conventional
# TOTP tolerance. Tighter than this rejects legitimate users on
# slightly-skewed phones; wider opens a small replay surface.
_TOTP_VALID_WINDOW = 1

_QR_BOX_SIZE = 8
_QR_BORDER = 2


def generate_secret() -> str:
    """Generate a random base32 TOTP secret (160-bit entropy).

    Returned format is the standard 32-character base32 string accepted
    by every authenticator app.
    """
    return pyotp.random_base32()


def verify_code(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code against ``secret``.

    Constant-time compare (delegated to pyotp). Accepts ±1 30-second
    window of clock skew. ``code`` is stripped of whitespace before
    verification — users frequently paste codes with stray spaces.
    """
    cleaned = code.strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) not in (6, 8):
        return False
    return bool(pyotp.TOTP(secret).verify(cleaned, valid_window=_TOTP_VALID_WINDOW))


def provisioning_uri(secret: str, account_email: str, issuer: str) -> str:
    """Build the ``otpauth://`` URI used by authenticator apps.

    Args:
        secret: Raw base32 TOTP secret.
        account_email: Used as the account label inside the
            authenticator app (so the user can tell apart entries for
            multiple tenants).
        issuer: App display name (e.g., ``Certora Risk``).
    """
    return pyotp.TOTP(secret).provisioning_uri(name=account_email, issuer_name=issuer)


def generate_qr_data_url(secret: str, account_email: str, issuer: str) -> str:
    """Render the provisioning URI as a base64 PNG data URL.

    The page embeds the result in an ``<img src=...>`` tag — no static
    file lifetime to manage.
    """
    uri = provisioning_uri(secret, account_email, issuer)
    qr = qrcode.QRCode(  # pyright: ignore[reportAttributeAccessIssue]
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,  # pyright: ignore[reportAttributeAccessIssue]
        box_size=_QR_BOX_SIZE,
        border=_QR_BORDER,
    )
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#202235", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")  # pyright: ignore[reportCallIssue]
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
