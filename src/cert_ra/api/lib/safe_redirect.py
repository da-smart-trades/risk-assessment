# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Open-redirect-safe target validation.

Used by OIDC sign-in's ``redirect_to`` query parameter and the
``/auth/reauth`` ``next`` parameter. Refuses anything that could redirect
the user off-site after authentication.
"""

from __future__ import annotations

DEFAULT_REDIRECT = "/dashboard"


def safe_redirect_target(target: str | None) -> str:
    r"""Return a same-origin path or the dashboard fallback.

    Rejects (returns ``DEFAULT_REDIRECT``):
      - ``None`` or empty input
      - Absolute URLs (anything not starting with ``/``)
      - Protocol-relative URLs (``//evil.com/...``)
      - Backslash-prefixed paths (``\\evil.com``) — some browsers
        normalize these as the host
      - Control characters (``< 0x20``) which clients may strip during
        URL normalization, potentially defeating naive prefix checks

    Args:
        target: Untrusted candidate path from a query string or form.

    Returns:
        Either ``target`` (if it's a clean same-origin path) or
        ``DEFAULT_REDIRECT``.
    """
    if not target or not target.startswith("/") or target.startswith("//"):
        return DEFAULT_REDIRECT
    if "\\" in target or any(ord(c) < 0x20 for c in target):
        return DEFAULT_REDIRECT
    return target
