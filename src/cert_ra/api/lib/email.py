# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from functools import cache

from litestar_email import BackendConfig, EmailConfig, ResendConfig

from cert_ra.settings.api import get_email_settings


@cache
def get_email_config() -> EmailConfig:
    """Create and return the email configuration based on settings.

    ``litestar_email`` takes the per-backend config (e.g. ``ResendConfig``)
    *as* the ``backend`` value, not as a separate keyword — so the resend
    API key rides on the ``backend`` field, never a ``resend=`` kwarg.
    """
    email = get_email_settings()
    backend_map = {"locmem": "memory"}
    backend_name = backend_map.get(email.backend, email.backend)
    backend: str | BackendConfig = backend_name
    if backend_name == "resend" and email.resend_api_key:
        backend = ResendConfig(api_key=email.resend_api_key)
    return EmailConfig(
        backend=backend,
        from_email=email.from_email,
        from_name=email.from_name,
    )
