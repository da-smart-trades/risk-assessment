# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for the Rated Network operator client (HTTP mocked)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from cert_ra.metrics.decentralization import rated
from cert_ra.settings.rated import RatedSettings

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


def _patch_settings(mocker: MockerFixture, **overrides: object) -> RatedSettings:
    defaults: dict[str, object] = {"api_key": "test-token", "page_size": 100}
    defaults.update(overrides)
    settings = RatedSettings(**defaults)  # type: ignore[arg-type]
    mocker.patch.object(rated, "get_rated_settings", return_value=settings)
    return settings


def _mock_transport(
    handler: httpx.MockTransport,
    mocker: MockerFixture,
) -> None:
    original = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = handler
        return original(*args, **kwargs)  # type: ignore[arg-type]

    mocker.patch.object(rated.httpx, "AsyncClient", side_effect=factory)


async def test_fetch_ethereum_operators_raises_without_api_key(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker, api_key=None)
    with pytest.raises(RuntimeError, match="no API key configured"):
        await rated.fetch_ethereum_operators()


async def test_fetch_ethereum_operators_parses_single_page(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker, page_size=100)

    payload = {
        "data": [
            {
                "operatorId": "lido",
                "name": "Lido",
                "validatorCount": 312000,
                "totalEffectiveBalance": 9_984_000.0,
            },
            {
                "operatorId": "coinbase",
                "name": "Coinbase",
                "validatorCount": 120000,
                "totalEffectiveBalance": 3_840_000.0,
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, json=payload)

    _mock_transport(httpx.MockTransport(handler), mocker)

    operators = await rated.fetch_ethereum_operators()

    assert [op.operator_id for op in operators] == ["lido", "coinbase"]
    assert operators[0].name == "Lido"
    assert operators[0].validator_count == 312000
    assert operators[0].total_effective_balance_eth == pytest.approx(9_984_000.0)


async def test_fetch_ethereum_operators_converts_gwei_to_eth(
    mocker: MockerFixture,
) -> None:
    # Defensive: Rated has historically returned effective balance in either
    # ETH or gwei. Values above 1e10 are treated as gwei and converted.
    _patch_settings(mocker, page_size=10)

    payload = {
        "data": [
            {
                "operatorId": "kraken",
                "name": "Kraken",
                "validatorCount": 5000,
                "totalEffectiveBalance": 160_000 * 1e9,  # 160k ETH in gwei
            }
        ]
    }

    _mock_transport(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)),
        mocker,
    )

    operators = await rated.fetch_ethereum_operators()
    assert operators[0].total_effective_balance_eth == pytest.approx(160_000.0)


async def test_fetch_ethereum_operators_paginates_until_short_page(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker, page_size=2)

    pages = iter(
        [
            {
                "data": [
                    {
                        "operatorId": "lido",
                        "name": "Lido",
                        "validatorCount": 312000,
                        "totalEffectiveBalance": 9_984_000.0,
                    },
                    {
                        "operatorId": "coinbase",
                        "name": "Coinbase",
                        "validatorCount": 120000,
                        "totalEffectiveBalance": 3_840_000.0,
                    },
                ]
            },
            {
                "data": [
                    {
                        "operatorId": "binance",
                        "name": "Binance",
                        "validatorCount": 80000,
                        "totalEffectiveBalance": 2_560_000.0,
                    }
                ]
            },
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(pages))

    _mock_transport(httpx.MockTransport(handler), mocker)

    operators = await rated.fetch_ethereum_operators()
    assert [op.operator_id for op in operators] == ["lido", "coinbase", "binance"]


async def test_fetch_ethereum_operators_skips_rows_without_id(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker, page_size=10)

    payload = {
        "data": [
            {"name": "missing id", "validatorCount": 1, "totalEffectiveBalance": 32.0},
            {
                "operatorId": "rocketpool",
                "name": "Rocket Pool",
                "validatorCount": 4000,
                "totalEffectiveBalance": 128_000.0,
            },
        ]
    }

    _mock_transport(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)),
        mocker,
    )

    operators = await rated.fetch_ethereum_operators()
    assert [op.operator_id for op in operators] == ["rocketpool"]
