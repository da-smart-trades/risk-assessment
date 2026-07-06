# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Query-param casing contract for the available-sub-categories endpoint.

The weighting-profile form populates its sub-category dropdown from
``GET /api/weighting-profiles/available-sub-categories?category=...``.
The ``category`` param is typed as :class:`WeightingProfileEntryCategory`,
whose values are uppercase (``ANCHOR`` / ``CONTROL`` / ``ASSURANCE``).

The form used to lowercase the value, which Litestar rejects with a
400 — and the ``fetch`` swallowed that into an empty option list, so
the dropdown never populated. This test pins the contract: the value
must be sent verbatim. No DB or auth needed; an isolated app is enough.
"""

from __future__ import annotations

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from cert_ra.types import (
    WeightingProfileEntryCategory,  # noqa: TC001  (runtime use by Litestar signature)
)


@get("/probe")
async def _probe(category: WeightingProfileEntryCategory) -> dict[str, str]:
    return {"category": category.value}


@pytest.mark.parametrize(
    ("value", "expected_status"),
    [
        ("ANCHOR", 200),
        ("CONTROL", 200),
        ("ASSURANCE", 200),
        ("anchor", 400),
        ("Control", 400),
    ],
)
def test_category_query_param_casing_contract(value: str, expected_status: int) -> None:
    """Uppercase enum values parse; any other casing is a 400."""
    with TestClient(app=Litestar([_probe])) as client:
        response = client.get("/probe", params={"category": value})
    assert response.status_code == expected_status
