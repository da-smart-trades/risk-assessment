# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for the decentralization activity helpers (fetch/compute wiring)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cert_ra.metrics.decentralization import activities

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# _compute
# ---------------------------------------------------------------------------


def test_compute_filters_zero_stakes_and_fills_all_fields() -> None:
    stakes = [("a", 100.0), ("b", 50.0), ("c", 0.0), ("d", 25.0)]
    result = activities._compute("ETHEREUM", stakes)  # noqa: SLF001

    assert result.chain == "ETHEREUM"
    assert result.number_of_nodes == 3
    assert result.total_amount_of_stakes == pytest.approx(175.0)
    assert result.nakamoto_liveness_coefficient == 1
    assert 0 < result.hhi <= 1.0
    # Three shapley slots present and sum to 1.
    shapleys = (
        result.shapley_top_value,
        result.shapley_second_value,
        result.shapley_third_value,
    )
    assert sum(shapleys) == pytest.approx(1.0)


def test_compute_raises_when_all_stakes_zero() -> None:
    with pytest.raises(RuntimeError, match="no positive-stake validators"):
        activities._compute("ETHEREUM", [("a", 0.0), ("b", 0.0)])  # noqa: SLF001


def test_compute_pads_shapley_when_fewer_than_three_validators() -> None:
    result = activities._compute("ETHEREUM", [("a", 60.0), ("b", 40.0)])  # noqa: SLF001

    # Only 2 validators → third slot is zero-padded.
    assert result.shapley_third_value == 0.0
    assert result.shapley_top_value + result.shapley_second_value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# fetch_decentralization routing
# ---------------------------------------------------------------------------


async def test_fetch_decentralization_rejects_unsupported_chain() -> None:
    with pytest.raises(ValueError, match="not supported"):
        await activities.fetch_decentralization("BASE")


async def test_fetch_decentralization_dispatches_to_chain_fetcher(
    mocker: MockerFixture,
) -> None:
    from cert_ra.types import ChainType

    fake_stakes = [("val1", 10.0), ("val2", 5.0)]

    async def _fake_fetcher() -> list[tuple[str, float]]:
        return fake_stakes

    # ``_STAKE_FETCHERS`` is built at import time with direct function
    # references, so patching the module-level name would not be picked up —
    # override the dict entry instead.
    mocker.patch.dict(
        activities._STAKE_FETCHERS,  # noqa: SLF001
        {ChainType.ETHEREUM: _fake_fetcher},
    )

    result = await activities.fetch_decentralization("ethereum")

    assert result.chain == "ETHEREUM"
    assert result.number_of_nodes == 2
    assert result.total_amount_of_stakes == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# fetch_operator_snapshot
# ---------------------------------------------------------------------------


async def test_fetch_operator_snapshot_rejects_unsupported_chain() -> None:
    with pytest.raises(ValueError, match="not supported"):
        await activities.fetch_operator_snapshot("BASE")


async def test_fetch_operator_snapshot_dispatches_per_chain(
    mocker: MockerFixture,
) -> None:
    from cert_ra.metrics.decentralization.rated import RatedOperator
    from cert_ra.types import ChainType

    sentinel = [
        RatedOperator(
            operator_id="op1",
            name="Op One",
            validator_count=10,
            total_effective_balance_eth=70.0,
            labeled=True,
        ),
        RatedOperator(
            operator_id="op2",
            name="Op Two",
            validator_count=5,
            total_effective_balance_eth=30.0,
            labeled=False,
        ),
    ]

    async def _fake_fetcher() -> list[RatedOperator]:
        return sentinel

    # Route SOLANA through the fake fetcher.
    mocker.patch.dict(
        activities._OPERATOR_FETCHERS,  # noqa: SLF001
        {ChainType.SOLANA: _fake_fetcher},
    )

    result = await activities.fetch_operator_snapshot("SOLANA")

    assert result.chain == "SOLANA"
    # Coverage for non-Ethereum chains = labeled stake / total stake.
    assert result.coverage_pct == pytest.approx(0.7)
    # Op One alone holds > 1/3 of stake → liveness coefficient is 1.
    assert result.entity_nakamoto_liveness == 1


async def test_fetch_operator_snapshot_builds_entries_and_coverage(
    mocker: MockerFixture,
) -> None:
    from cert_ra.metrics.decentralization.rated import RatedOperator

    fake_operators = [
        RatedOperator(
            operator_id="lido",
            name="Lido",
            validator_count=300,
            total_effective_balance_eth=600.0,
        ),
        RatedOperator(
            operator_id="coinbase",
            name="Coinbase",
            validator_count=200,
            total_effective_balance_eth=200.0,
        ),
        RatedOperator(
            operator_id="solo",
            name="Solo Stakers",
            validator_count=100,
            total_effective_balance_eth=100.0,
        ),
    ]
    fake_beacon = [("v1", 600.0), ("v2", 200.0), ("v3", 100.0), ("unlabeled", 100.0)]

    async def _fake_rated() -> list[RatedOperator]:
        return fake_operators

    async def _fake_beacon() -> list[tuple[str, float]]:
        return fake_beacon

    # Activity reads the operator fetcher from a module-level dict captured
    # at import time — patch the dict entry rather than the bare symbol.
    from cert_ra.types import ChainType

    mocker.patch.dict(
        activities._OPERATOR_FETCHERS,  # noqa: SLF001
        {ChainType.ETHEREUM: _fake_rated},
    )
    mocker.patch.object(activities, "fetch_ethereum_stakes", _fake_beacon)

    result = await activities.fetch_operator_snapshot("ETHEREUM")

    assert result.chain == "ETHEREUM"
    assert result.entity_count == 3
    # Rated reports 900 ETH; Beacon shows 1000 → coverage 0.9.
    assert result.coverage_pct == pytest.approx(0.9)
    # Top operator is Lido with 600/900 share.
    assert result.top_operators[0].operator_id == "lido"
    assert result.top_operators[0].stake_share == pytest.approx(600 / 900)
    # Lido alone holds > 1/3 of stake → liveness coefficient is 1.
    assert result.entity_nakamoto_liveness == 1


async def test_fetch_operator_snapshot_raises_when_rated_empty(
    mocker: MockerFixture,
) -> None:
    from cert_ra.metrics.decentralization.rated import RatedOperator
    from cert_ra.types import ChainType

    async def _empty() -> list[RatedOperator]:
        return []

    mocker.patch.dict(
        activities._OPERATOR_FETCHERS,  # noqa: SLF001
        {ChainType.ETHEREUM: _empty},
    )

    with pytest.raises(RuntimeError, match="no operators returned"):
        await activities.fetch_operator_snapshot("ETHEREUM")
