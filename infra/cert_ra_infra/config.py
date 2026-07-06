# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Loader for the bootstrap deployment configuration.

Deployer-specific values (GitHub owner/repo, per-environment AWS region, public
domain, Route53 hosted-zone id, VPC sizing, and the container registry) live in a
JSON file at the repository root rather than being hardcoded in the CDK source, so
a new owner can point the stacks at their own account without editing Python.

Resolution order:

1. ``$CERT_RA_DEPLOYMENT_CONFIG`` — explicit path override (used by CI if needed).
2. ``deployment.config.json`` — the real, git-ignored config the deployer creates
   by copying the example and filling in their values.
3. ``deployment.config.example.json`` — the tracked template with placeholders.
   Falling back to it keeps a fresh clone (and the test suite) working out of the box.

The file is searched for by walking up from this module and from the current working
directory, so it is found regardless of where CDK is invoked from.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_ENV_OVERRIDE = "CERT_RA_DEPLOYMENT_CONFIG"
_REAL_NAME = "deployment.config.json"
_EXAMPLE_NAME = "deployment.config.example.json"


def _search_roots() -> list[Path]:
    """Directories to search, nearest first: this module's parents then CWD's."""
    here = Path(__file__).resolve()
    cwd = Path.cwd().resolve()
    ordered: list[Path] = [here.parent, *here.parents, cwd, *cwd.parents]
    seen: set[Path] = set()
    roots: list[Path] = []
    for path in ordered:
        if path not in seen:
            seen.add(path)
            roots.append(path)
    return roots


def _locate() -> Path:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        path = Path(override)
        if not path.exists():
            msg = f"{_ENV_OVERRIDE}={override!r} does not exist"
            raise FileNotFoundError(msg)
        return path
    for name in (_REAL_NAME, _EXAMPLE_NAME):
        for root in _search_roots():
            candidate = root / name
            if candidate.exists():
                return candidate
    msg = (
        f"Could not find {_REAL_NAME} or {_EXAMPLE_NAME}. Copy "
        f"{_EXAMPLE_NAME} to {_REAL_NAME} at the repository root and fill in "
        f"your values, or set ${_ENV_OVERRIDE}."
    )
    raise FileNotFoundError(msg)


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Return the parsed deployment configuration (cached)."""
    with _locate().open(encoding="utf-8") as handle:
        return json.load(handle)
