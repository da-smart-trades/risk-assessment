# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""HTTP-mocked tests for the per-chain operator fetchers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from cert_ra.metrics.decentralization import (
    avalanche_operators,
    polygon_operators,
    solana_operators,
)
from cert_ra.settings.rpc import RPCSettings

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


def _mock_transport(
    handler: httpx.MockTransport, mocker: MockerFixture, module: object
) -> None:
    original = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = handler
        return original(*args, **kwargs)  # type: ignore[arg-type]

    mocker.patch.object(module.httpx, "AsyncClient", side_effect=factory)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Polygon
# ---------------------------------------------------------------------------


async def test_polygon_operators_uses_api_name_and_marks_labeled(
    mocker: MockerFixture,
) -> None:
    page = {
        "success": True,
        "result": [
            {
                "id": 1,
                "name": "Stake.fish",
                "owner": "0xabc",
                "signer": "0xdef",
                "totalStaked": 1_000_000_000_000_000_000_000,  # 1000 POL
            },
            {
                "id": 2,
                "name": "",  # falls back, marked unlabeled
                "owner": "0xnoname",
                "signer": "0xnoname",
                "totalStaked": 500_000_000_000_000_000_000,  # 500 POL
            },
        ],
        "summary": {"total": 2, "size": 2},
    }
    _mock_transport(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=page)),
        mocker,
        polygon_operators,
    )
    # Empty labels override — exercise the API-name + fallback paths.
    mocker.patch.object(polygon_operators, "labels_for", return_value={})

    operators = await polygon_operators.fetch_polygon_operators()

    assert operators[0].name == "Stake.fish"
    assert operators[0].labeled is True
    assert operators[0].total_effective_balance_eth == pytest.approx(1000.0)
    assert operators[1].labeled is False
    assert operators[1].name.startswith("Validator ")


async def test_polygon_operators_applies_curated_override(
    mocker: MockerFixture,
) -> None:
    page = {
        "success": True,
        "result": [
            {
                "id": 7,
                "name": "Acme Staker",
                "owner": "0xABCDEF",
                "signer": "0x123",
                "totalStaked": 100_000_000_000_000_000_000,  # 100 POL
            }
        ],
        "summary": {"total": 1, "size": 1},
    }
    _mock_transport(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=page)),
        mocker,
        polygon_operators,
    )
    mocker.patch.object(
        polygon_operators, "labels_for", return_value={"0xabcdef": "Coinbase"}
    )

    operators = await polygon_operators.fetch_polygon_operators()
    # Curated override beats the API-supplied name.
    assert operators[0].name == "Coinbase"
    assert operators[0].labeled is True


# ---------------------------------------------------------------------------
# Avalanche
# ---------------------------------------------------------------------------


def _patch_avalanche_rpc(mocker: MockerFixture, urls: list[str]) -> None:
    settings = RPCSettings(avalanche_p_public_rpcs=urls)  # type: ignore[arg-type]
    mocker.patch.object(avalanche_operators, "get_rpc_settings", return_value=settings)


async def test_avalanche_operators_groups_by_reward_address(
    mocker: MockerFixture,
) -> None:
    _patch_avalanche_rpc(mocker, ["https://avax.example"])
    mocker.patch.object(avalanche_operators, "labels_for", return_value={})

    payload = {
        "result": {
            "validators": [
                {
                    "nodeID": "NodeID-A",
                    "rewardAddress": "P-avax1coinbase",
                    "stakeAmount": 2_000_000_000,
                    "delegatorWeight": 0,
                },
                {
                    "nodeID": "NodeID-B",
                    "rewardAddress": "P-avax1coinbase",  # same operator
                    "stakeAmount": 3_000_000_000,
                    "delegatorWeight": 1_000_000_000,
                },
                {
                    "nodeID": "NodeID-C",
                    "rewardAddress": "P-avax1solo",
                    "stakeAmount": 1_000_000_000,
                    "delegatorWeight": 0,
                },
            ]
        }
    }

    _mock_transport(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)),
        mocker,
        avalanche_operators,
    )

    operators = await avalanche_operators.fetch_avalanche_operators()
    by_key = {op.operator_id: op for op in operators}

    assert by_key["P-avax1coinbase"].validator_count == 2
    # 2 + 3 + 1 (delegator) = 6 AVAX
    assert by_key["P-avax1coinbase"].total_effective_balance_eth == pytest.approx(6.0)
    # No curated label → marked unlabeled, short-form display.
    assert by_key["P-avax1coinbase"].labeled is False
    assert by_key["P-avax1coinbase"].name.startswith("P-avax1")


async def test_avalanche_operators_applies_curated_label(
    mocker: MockerFixture,
) -> None:
    _patch_avalanche_rpc(mocker, ["https://avax.example"])
    mocker.patch.object(
        avalanche_operators,
        "labels_for",
        return_value={"P-avax1coinbase": "Coinbase Cloud"},
    )

    payload = {
        "result": {
            "validators": [
                {
                    "nodeID": "NodeID-A",
                    "rewardAddress": "P-avax1coinbase",
                    "stakeAmount": 2_000_000_000,
                    "delegatorWeight": 0,
                }
            ]
        }
    }

    _mock_transport(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)),
        mocker,
        avalanche_operators,
    )

    operators = await avalanche_operators.fetch_avalanche_operators()
    assert operators[0].name == "Coinbase Cloud"
    assert operators[0].labeled is True


# ---------------------------------------------------------------------------
# Solana
# ---------------------------------------------------------------------------


def _patch_solana_rpc(mocker: MockerFixture, urls: list[str]) -> None:
    settings = RPCSettings(solana_public_rpcs=urls)  # type: ignore[arg-type]
    mocker.patch.object(solana_operators, "get_rpc_settings", return_value=settings)


async def test_solana_operators_groups_by_node_pubkey(mocker: MockerFixture) -> None:
    _patch_solana_rpc(mocker, ["https://sol.example"])
    mocker.patch.object(solana_operators, "labels_for", return_value={})

    payload = {
        "result": {
            "current": [
                {
                    "votePubkey": "VOTE_1",
                    "nodePubkey": "NODE_A",
                    "activatedStake": 1_000_000_000,
                },
                {
                    "votePubkey": "VOTE_2",
                    "nodePubkey": "NODE_A",  # same node → grouped
                    "activatedStake": 2_000_000_000,
                },
                {
                    "votePubkey": "VOTE_3",
                    "nodePubkey": "NODE_B",
                    "activatedStake": 500_000_000,
                },
            ]
        }
    }

    _mock_transport(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)),
        mocker,
        solana_operators,
    )

    operators = await solana_operators.fetch_solana_operators()
    by_key = {op.operator_id: op for op in operators}

    assert by_key["NODE_A"].validator_count == 2
    assert by_key["NODE_A"].total_effective_balance_eth == pytest.approx(3.0)
    assert by_key["NODE_A"].labeled is False  # no curated label


async def test_solana_operators_applies_curated_label(mocker: MockerFixture) -> None:
    _patch_solana_rpc(mocker, ["https://sol.example"])
    mocker.patch.object(
        solana_operators, "labels_for", return_value={"NODE_A": "Helius"}
    )

    payload = {
        "result": {
            "current": [
                {
                    "votePubkey": "VOTE_1",
                    "nodePubkey": "NODE_A",
                    "activatedStake": 1_000_000_000,
                }
            ]
        }
    }

    _mock_transport(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)),
        mocker,
        solana_operators,
    )

    operators = await solana_operators.fetch_solana_operators()
    assert operators[0].name == "Helius"
    assert operators[0].labeled is True
