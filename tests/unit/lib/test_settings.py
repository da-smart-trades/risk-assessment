# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

import pytest

from cert_ra.settings.api import get_app_settings

pytestmark = pytest.mark.anyio


def test_app_slug() -> None:
    """Test app name conversion to slug."""
    settings = get_app_settings()
    settings.name = "My Application"
    assert settings.slug == "my-application"
