# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from pydantic import BaseModel


class ReleaseParams(BaseModel):
    """Workflow input identifying which GitHub repository to poll for releases."""

    chain: str
    repo: str  # ``owner/name`` GitHub format, e.g. ``"ethereum/go-ethereum"``


class ReleaseResult(BaseModel):
    """Latest release observed for a ``(chain, repo)`` pair."""

    chain: str
    repo: str
    released_at: str  # ISO-8601 datetime string from GitHub
