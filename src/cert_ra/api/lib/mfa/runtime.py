# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Runtime helpers for the WebAuthn RP configuration.

Derives the relying-party ID and expected origin from
``AppSettings.url`` so the controller doesn't have to parse URLs.
"""

from __future__ import annotations

from urllib.parse import urlparse

from cert_ra.settings.api import get_app_settings


def rp_id() -> str:
    """The WebAuthn relying-party identifier (bare hostname).

    For ``url=https://app.certora.com:8443/`` returns ``app.certora.com``.
    Authenticators bind credentials to this value, so it must be stable
    across deploys.
    """
    parsed = urlparse(get_app_settings().url)
    return parsed.hostname or "localhost"


def expected_origin() -> str:
    """The expected ``window.location.origin`` for WebAuthn ceremonies.

    Includes scheme + host + port. Must match what the browser sees.
    """
    parsed = urlparse(get_app_settings().url)
    if parsed.port:
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    return f"{parsed.scheme}://{parsed.hostname}"


def rp_name() -> str:
    """Human-readable RP name shown by the authenticator UI."""
    return get_app_settings().name
