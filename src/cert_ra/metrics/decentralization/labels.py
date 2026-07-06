# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Curated operator label loader.

Reads ``operator_labels.json`` and exposes per-chain ``{identifier: name}``
dicts to the per-chain operator fetchers. Keys prefixed with ``_`` are
documentation entries and are ignored.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

_LABELS_PATH = Path(__file__).parent / "operator_labels.json"


@cache
def _load() -> dict[str, dict[str, str]]:
    raw = json.loads(_LABELS_PATH.read_text())
    out: dict[str, dict[str, str]] = {}
    for chain, mapping in raw.items():
        if not isinstance(mapping, dict) or chain.startswith("_"):
            continue
        out[chain] = {k: v for k, v in mapping.items() if not k.startswith("_")}
    return out


def labels_for(chain: str) -> dict[str, str]:
    """Return the ``{identifier: name}`` map for ``chain`` (empty if absent)."""
    return _load().get(chain.upper(), {})
