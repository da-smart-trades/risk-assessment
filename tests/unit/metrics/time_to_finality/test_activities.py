# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for time-to-finality helpers and activity routing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cert_ra.metrics.time_to_finality import activities
from cert_ra.settings.rpc import RPCSettings
from cert_ra.types import ChainType

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# _to_ws
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("http_url", "expected"),
    [
        ("https://rpc.example.com", "wss://rpc.example.com"),
        ("http://localhost:8545", "ws://localhost:8545"),
        ("wss://already-secure.example", "wss://already-secure.example"),
    ],
)
def test_to_ws_replaces_scheme(http_url: str, expected: str) -> None:
    assert activities._to_ws(http_url) == expected  # noqa: SLF001


# ---------------------------------------------------------------------------
# _mean_gap
# ---------------------------------------------------------------------------


def test_mean_gap_with_uniform_spacing() -> None:
    assert activities._mean_gap([0.0, 1.0, 2.0, 3.0]) == pytest.approx(1.0)  # noqa: SLF001


def test_mean_gap_with_variable_spacing() -> None:
    # Gaps: 1, 2, 3 → mean = 2.
    assert activities._mean_gap([0.0, 1.0, 3.0, 6.0]) == pytest.approx(2.0)  # noqa: SLF001


def test_mean_gap_raises_with_insufficient_samples() -> None:
    with pytest.raises(RuntimeError, match="not enough messages"):
        activities._mean_gap([1.0])  # noqa: SLF001


# ---------------------------------------------------------------------------
# _urls_for_chain
# ---------------------------------------------------------------------------


def test_urls_for_chain_uses_chain_specific_list(mocker: MockerFixture) -> None:
    settings = RPCSettings(
        ethereum_public_rpcs=["https://eth.example"],
        base_public_rpcs=["https://base.example"],
        solana_public_rpcs=["https://sol.example"],
        ink_url="https://ink.example",
        unichain_url="https://uni.example",
    )
    mocker.patch.object(activities, "get_rpc_settings", return_value=settings)

    assert activities._urls_for_chain(ChainType.ETHEREUM) == ["https://eth.example"]  # noqa: SLF001
    assert activities._urls_for_chain(ChainType.BASE) == ["https://base.example"]  # noqa: SLF001
    assert activities._urls_for_chain(ChainType.SOLANA) == ["https://sol.example"]  # noqa: SLF001
    assert activities._urls_for_chain(ChainType.INK) == ["https://ink.example"]  # noqa: SLF001
    assert activities._urls_for_chain(ChainType.UNICHAIN) == ["https://uni.example"]  # noqa: SLF001


def test_urls_for_chain_empty_for_unconfigured_ink(mocker: MockerFixture) -> None:
    mocker.patch.object(
        activities, "get_rpc_settings", return_value=RPCSettings(ink_url="")
    )
    assert activities._urls_for_chain(ChainType.INK) == []  # noqa: SLF001


def test_urls_for_chain_returns_empty_for_unsupported_chain(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(activities, "get_rpc_settings", return_value=RPCSettings())
    assert activities._urls_for_chain(ChainType.ARBITRUM) == []  # noqa: SLF001


# ---------------------------------------------------------------------------
# fetch_time_to_finality (routing + error paths)
# ---------------------------------------------------------------------------


async def test_fetch_time_to_finality_rejects_unsupported_chain() -> None:
    with pytest.raises(ValueError, match="not supported"):
        await activities.fetch_time_to_finality("ARBITRUM")


async def test_fetch_time_to_finality_raises_when_no_urls(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(activities, "_urls_for_chain", return_value=[])
    with pytest.raises(RuntimeError, match="no RPC URL configured"):
        await activities.fetch_time_to_finality("ETHEREUM")


async def test_fetch_time_to_finality_returns_ethereum_result(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        activities, "_urls_for_chain", return_value=["https://eth.example"]
    )

    async def fake_eth(ws_url: str, method: str) -> float:
        assert method == "newHeads"
        assert ws_url.startswith("wss://")
        return 12.5

    mocker.patch.object(activities, "_eth_soft_finality", side_effect=fake_eth)

    result = await activities.fetch_time_to_finality("ethereum")
    assert result.chain == "ETHEREUM"
    assert result.soft_finality_seconds == pytest.approx(12.5)


async def test_fetch_time_to_finality_uses_flashblocks_for_base(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        activities, "_urls_for_chain", return_value=["https://base.example"]
    )
    calls: list[str] = []

    async def fake_eth(_ws_url: str, method: str) -> float:
        calls.append(method)
        return 0.25

    mocker.patch.object(activities, "_eth_soft_finality", side_effect=fake_eth)

    result = await activities.fetch_time_to_finality("BASE")
    assert calls == ["newFlashblocks"]
    assert result.soft_finality_seconds == pytest.approx(0.25)


async def test_fetch_time_to_finality_uses_solana_helper(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        activities, "_urls_for_chain", return_value=["https://sol.example"]
    )
    mocker.patch.object(activities, "_solana_soft_finality", return_value=0.4)
    # Guard: eth helper must not be invoked on Solana.
    eth_mock = mocker.patch.object(activities, "_eth_soft_finality")

    result = await activities.fetch_time_to_finality("solana")
    assert result.chain == "SOLANA"
    assert result.soft_finality_seconds == pytest.approx(0.4)
    eth_mock.assert_not_called()


async def test_fetch_time_to_finality_falls_back_to_next_url(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        activities,
        "_urls_for_chain",
        return_value=["https://primary.example", "https://fallback.example"],
    )

    call_count = {"n": 0}

    async def fake_eth(ws_url: str, _method: str) -> float:
        call_count["n"] += 1
        if "primary" in ws_url:
            msg = "primary ws failed"
            raise RuntimeError(msg)
        return 7.0

    mocker.patch.object(activities, "_eth_soft_finality", side_effect=fake_eth)

    result = await activities.fetch_time_to_finality("ETHEREUM")
    assert call_count["n"] == 2
    assert result.soft_finality_seconds == pytest.approx(7.0)


async def test_fetch_time_to_finality_raises_when_all_urls_fail(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        activities,
        "_urls_for_chain",
        return_value=["https://a.example", "https://b.example"],
    )

    async def fake_eth(_ws_url: str, _method: str) -> float:
        msg = "ws failed"
        raise RuntimeError(msg)

    mocker.patch.object(activities, "_eth_soft_finality", side_effect=fake_eth)

    with pytest.raises(RuntimeError, match="all websocket nodes failed"):
        await activities.fetch_time_to_finality("ETHEREUM")
