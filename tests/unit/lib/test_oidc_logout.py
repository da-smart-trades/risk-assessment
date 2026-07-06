# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unit tests for RP-initiated logout URL construction."""

from __future__ import annotations

import pytest

from cert_ra.api.lib.oidc.providers import get_end_session_url

pytestmark = pytest.mark.anyio

_REDIRECT = "https://app.example.com/login/"


async def test_github_has_no_end_session() -> None:
    """GitHub is OAuth2-only (no discovery) → no RP-logout URL."""
    assert await get_end_session_url("github", _REDIRECT) is None


async def test_unknown_provider_returns_none() -> None:
    """An unrecognised provider value yields no URL (never raises)."""
    assert await get_end_session_url("facebook", _REDIRECT) is None


async def test_unconfigured_provider_returns_none() -> None:
    """A provider with no client credentials short-circuits before network."""
    # google has a discovery_url but no client_id in the test env, so the
    # helper returns None without attempting a discovery fetch.
    assert await get_end_session_url("google", _REDIRECT) is None
