# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for token-activity collection — supply/flow decoupling in particular."""

# ruff: noqa: SLF001 — this module exercises the collector's internal helpers.
# ruff: noqa: S106 — the ``token=`` kwarg is a TokenType value, not a secret.

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from cert_ra.metrics.tokens import activities
from cert_ra.metrics.tokens.schemas import TokenActivityParams

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


def _patch_supply(mocker: MockerFixture, value: float) -> None:
    mocker.patch.object(
        activities, "_fetch_total_supply", AsyncMock(return_value=value)
    )


# ---------------------------------------------------------------------------
# _project_row: supply is always emitted; flow only when a Dune row is present
# ---------------------------------------------------------------------------


def test_project_row_without_flow_emits_supply_only() -> None:
    out = activities._project_row("ETHEREUM", "LINK", None, 500.0)
    assert len(out) == 1
    assert out[0].metric_type == "ETH_LINK_TOTAL_SUPPLY"
    assert out[0].value == 500.0


def test_project_row_with_flow_emits_full_panel() -> None:
    row = {
        "transfer_count": 5,
        "unique_addresses": 7,
        "inflow": 0,
        "outflow": 0,
        "volume": 99,
    }
    out = activities._project_row("ETHEREUM", "LINK", row, 500.0)
    by_type = {r.metric_type: r.value for r in out}
    assert by_type == {
        "ETH_LINK_TOTAL_SUPPLY": 500.0,
        "ETH_LINK_TRANSFER_COUNT": 5.0,
        "ETH_LINK_UNIQUE_ADDRESSES": 7.0,
        "ETH_LINK_VOLUME": 99.0,
    }


# ---------------------------------------------------------------------------
# fetch_token_activity: a Dune failure degrades to a supply-only snapshot
# ---------------------------------------------------------------------------


async def test_fetch_degrades_to_supply_only_when_dune_fails(
    mocker: MockerFixture,
) -> None:
    _patch_supply(mocker, 1234.5)
    mocker.patch.object(
        activities, "run_dune_query", AsyncMock(side_effect=RuntimeError("401"))
    )

    batch = await activities.fetch_token_activity(
        TokenActivityParams(chain="ETHEREUM", token="LINK")
    )
    assert len(batch.results) == 1
    assert batch.results[0].metric_type == "ETH_LINK_TOTAL_SUPPLY"
    assert batch.results[0].value == 1234.5


async def test_fetch_returns_full_panel_when_dune_succeeds(
    mocker: MockerFixture,
) -> None:
    _patch_supply(mocker, 1000.0)
    mocker.patch.object(
        activities,
        "run_dune_query",
        AsyncMock(
            return_value=[
                {
                    "transfer_count": 3,
                    "unique_addresses": 4,
                    "inflow": 0,
                    "outflow": 0,
                    "volume": 50,
                }
            ]
        ),
    )

    batch = await activities.fetch_token_activity(
        TokenActivityParams(chain="ETHEREUM", token="LINK")
    )
    by_type = {r.metric_type: r.value for r in batch.results}
    assert by_type == {
        "ETH_LINK_TOTAL_SUPPLY": 1000.0,
        "ETH_LINK_TRANSFER_COUNT": 3.0,
        "ETH_LINK_UNIQUE_ADDRESSES": 4.0,
        "ETH_LINK_VOLUME": 50.0,
    }


async def test_fetch_supply_call_is_independent_of_dune(
    mocker: MockerFixture,
) -> None:
    """USDC keeps its total-supply metric even when the Dune flow query dies."""
    _patch_supply(mocker, 42.0)
    mocker.patch.object(
        activities, "run_dune_query", AsyncMock(side_effect=RuntimeError("boom"))
    )

    batch = await activities.fetch_token_activity(
        TokenActivityParams(chain="ETHEREUM", token="USDC")
    )
    types = {r.metric_type for r in batch.results}
    assert types == {"USDC_TOTAL_SUPPLY"}
    assert batch.results[0].value == 42.0


async def test_fetch_propagates_supply_failure(mocker: MockerFixture) -> None:
    """An on-chain supply failure should raise (Temporal retries), not degrade."""
    mocker.patch.object(
        activities,
        "_fetch_total_supply",
        AsyncMock(side_effect=RuntimeError("rpc down")),
    )
    dune = mocker.patch.object(activities, "run_dune_query", AsyncMock())

    with pytest.raises(RuntimeError, match="rpc down"):
        await activities.fetch_token_activity(
            TokenActivityParams(chain="ETHEREUM", token="LINK")
        )
    dune.assert_not_called()  # supply is fetched first; Dune is never reached
