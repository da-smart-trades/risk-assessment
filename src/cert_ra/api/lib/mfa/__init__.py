# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""MFA library — TOTP, WebAuthn passkeys, recovery codes.

Wraps ``pyotp`` and ``webauthn`` so the controllers depend on a small,
project-local surface rather than the third-party APIs directly.
"""

from __future__ import annotations
