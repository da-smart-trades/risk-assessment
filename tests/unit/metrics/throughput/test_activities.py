# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for throughput dispatch: EVM via RPC, Solana via Dune."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cert_ra.metrics.throughput import activities
from cert_ra.metrics.throughput.schemas import SUPPORTED_CHAINS, ThroughputResult

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Dispatch table sanity
# ---------------------------------------------------------------------------


def test_evm_chains_covers_all_supported_chains_except_solana() -> None:
    # SOLANA is sourced from Dune and CANTON from the Splice Scan API; every
    # other supported chain must have an EVM JSON-RPC fetcher entry.
    non_evm = {"SOLANA", "CANTON"}
    expected = {c for c in SUPPORTED_CHAINS if c not in non_evm}
    assert set(activities._EVM_CHAINS.keys()) == expected  # noqa: SLF001


def test_solana_dune_query_uses_fee_and_block_slot() -> None:
    query = activities._build_solana_dune_query()  # noqa: SLF001
    assert "avg(fee)" in query
    assert "block_slot" in query
    assert "solana.transactions" in query


# ---------------------------------------------------------------------------
# fetch_throughput dispatch
# ---------------------------------------------------------------------------


async def test_fetch_throughput_rejects_unsupported_chain() -> None:
    with pytest.raises(ValueError, match="not supported"):
        await activities.fetch_throughput("BITCOIN")


@pytest.mark.parametrize(
    ("chain", "expected_slot", "urls_attr"),
    [
        ("ETHEREUM", 12.0, "ethereum_urls"),
        ("ARBITRUM", 0.25, "arbitrum_urls"),
        ("BASE", 2.0, "base_urls"),
        ("POLYGON", 2.1, "polygon_urls"),
        ("OPTIMISM", 2.0, "optimism_urls"),
        ("AVALANCHE_C", 2.0, "avalanche_c_urls"),
    ],
)
async def test_fetch_throughput_evm_chain_dispatches_to_rpc(
    mocker: MockerFixture, chain: str, expected_slot: float, urls_attr: str
) -> None:
    """Each multi-URL EVM chain reaches ``fetch_evm_throughput`` with the right
    slot time and the URL list from the corresponding settings property.
    """
    expected = ThroughputResult(
        chain=chain,
        gas_price=1.0,
        transactions_per_second=2.0,
        blocks_per_second=3.0,
    )
    rpc_mock = mocker.patch.object(
        activities, "fetch_evm_throughput", return_value=expected
    )
    # ``run_dune_query`` should never be called for EVM chains.
    dune_mock = mocker.patch.object(activities, "run_dune_query")

    fake_urls = [
        f"https://{chain.lower()}-1.example",
        f"https://{chain.lower()}-2.example",
    ]
    fake_settings = type(
        "FakeSettings",
        (),
        {urls_attr: fake_urls, "ink_url": "x", "unichain_url": "x"},
    )()
    mocker.patch.object(activities, "get_rpc_settings", return_value=fake_settings)

    result = await activities.fetch_throughput(chain.lower())

    assert result is expected
    rpc_mock.assert_awaited_once_with(chain, expected_slot, fake_urls)
    dune_mock.assert_not_called()


@pytest.mark.parametrize(
    ("chain", "expected_slot", "url_attr"),
    [
        ("INK", 1.0, "ink_url"),
        ("UNICHAIN", 1.0, "unichain_url"),
    ],
)
async def test_fetch_throughput_single_url_chain_wraps_url(
    mocker: MockerFixture, chain: str, expected_slot: float, url_attr: str
) -> None:
    """Ink / Unichain expose a single ``*_url`` string; dispatch must wrap it
    into a one-element list before calling ``fetch_evm_throughput``.
    """
    expected = ThroughputResult(
        chain=chain, gas_price=0.0, transactions_per_second=0.0, blocks_per_second=0.0
    )
    rpc_mock = mocker.patch.object(
        activities, "fetch_evm_throughput", return_value=expected
    )

    fake_url = f"https://{chain.lower()}.example"
    fake_settings = type("FakeSettings", (), {url_attr: fake_url})()
    mocker.patch.object(activities, "get_rpc_settings", return_value=fake_settings)

    await activities.fetch_throughput(chain)

    rpc_mock.assert_awaited_once_with(chain, expected_slot, [fake_url])


# ---------------------------------------------------------------------------
# Solana — still Dune
# ---------------------------------------------------------------------------


async def test_fetch_throughput_solana_parses_dune_row(mocker: MockerFixture) -> None:
    mocker.patch.object(
        activities,
        "run_dune_query",
        return_value=[
            {
                "avg_gas_price": 5000.0,
                "transactions_per_second": 2500.0,
                "blocks_per_second": 2.5,
            }
        ],
    )
    rpc_mock = mocker.patch.object(activities, "fetch_evm_throughput")

    result = await activities.fetch_throughput("solana")

    assert result.chain == "SOLANA"
    assert result.gas_price == pytest.approx(5000.0)
    assert result.transactions_per_second == pytest.approx(2500.0)
    assert result.blocks_per_second == pytest.approx(2.5)
    rpc_mock.assert_not_called()


async def test_fetch_throughput_solana_raises_on_empty_rows(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(activities, "run_dune_query", return_value=[])
    with pytest.raises(RuntimeError, match="no rows returned from Dune for SOLANA"):
        await activities.fetch_throughput("SOLANA")


async def test_fetch_throughput_solana_raises_on_null_values(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        activities,
        "run_dune_query",
        return_value=[
            {
                "avg_gas_price": None,
                "transactions_per_second": 1.0,
                "blocks_per_second": 1.0,
            }
        ],
    )
    with pytest.raises(RuntimeError, match="null values from Dune for SOLANA"):
        await activities.fetch_throughput("SOLANA")
