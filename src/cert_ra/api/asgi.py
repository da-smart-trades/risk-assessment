# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from litestar import Litestar

from .core import ApplicationCore


def create_app() -> Litestar:
    """Create ASGI application.

    Returns:
        The ASGI application.
    """
    app_core = ApplicationCore()

    return Litestar(plugins=[app_core])
