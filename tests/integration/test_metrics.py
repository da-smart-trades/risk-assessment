# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cert_ra.db.models import Decentralization, Throughput, TimeToFinality
from cert_ra.db.models.finality import (
    FinalityEthereum,
    FinalityEvmL2,
    FinalityOpStack,
    FinalityPolygon,
    FinalitySolana,
)
from cert_ra.types import ChainType

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

_METRICS_PATHS = [
    "/metrics/chains",
    "/metrics/finality/ethereum",
    "/metrics/finality/evm-l2",
    "/metrics/finality/op-stack",
    "/metrics/finality/polygon",
    "/metrics/finality/solana",
    "/metrics/throughput",
    "/metrics/time-to-finality",
    "/metrics/decentralization",
]


@pytest.fixture(autouse=True)
async def _seed_finality(session: AsyncSession) -> None:
    """Insert one finality snapshot per chain into the test database."""
    session.add_all(
        [
            FinalityEthereum(
                head_height=20_000_000,
                finalized_height=19_999_936,
                safe_height=19_999_968,
                justified_epoch=625_000,
                finalized_epoch=624_999,
                justified_finalized_gap=1,
                time_since_finality_advance=5.0,
                head_to_finalized_time=768,
            ),
            FinalityEvmL2(
                chain=ChainType.ARBITRUM,
                latest_height=250_000_000,
                safe_height=249_999_990,
                finalized_height=249_999_980,
                latest_to_safe_blocks=10,
                safe_to_finalized_blocks=10,
                time_since_last_head=0.5,
                height_correlation=20,
                time_to_hard_finality=1200,
            ),
            FinalityEvmL2(
                chain=ChainType.BASE,
                latest_height=30_000_000,
                safe_height=29_999_900,
                finalized_height=29_999_800,
                latest_to_safe_blocks=100,
                safe_to_finalized_blocks=100,
                time_since_last_head=0.8,
            ),
            FinalityOpStack(
                chain=ChainType.INK,
                unsafe_height=5_000_000,
                safe_height=4_999_900,
                finalized_height=4_999_800,
                unsafe_to_safe_blocks=100,
                safe_to_finalized_blocks=100,
                time_since_last_unsafe=1.0,
                height_correlation=200,
                time_to_hard_finality=3600,
            ),
            FinalityOpStack(
                chain=ChainType.UNICHAIN,
                unsafe_height=3_000_000,
                safe_height=2_999_900,
                finalized_height=2_999_800,
                unsafe_to_safe_blocks=100,
                safe_to_finalized_blocks=100,
                time_since_last_unsafe=0.5,
                height_correlation=200,
                time_to_hard_finality=3600,
            ),
            FinalityPolygon(
                latest_height=60_000_000,
                finalized_height=59_999_900,
                latest_to_finalized_blocks=100,
                time_since_last_head=1.0,
            ),
            FinalitySolana(
                processed_slot=300_000_000,
                confirmed_slot=299_999_990,
                finalized_slot=299_999_950,
                confirmed_finalized_gap=40,
                processed_confirmed_gap=10,
            ),
            Throughput(
                chain=ChainType.ARBITRUM,
                gas_price=0.1,
                transactions_per_second=20.0,
                blocks_per_second=4.0,
            ),
            Throughput(
                chain=ChainType.SOLANA,
                gas_price=5000.0,
                transactions_per_second=2500.0,
                blocks_per_second=2.5,
            ),
            TimeToFinality(
                chain=ChainType.ETHEREUM,
                soft_finality_seconds=12.1,
            ),
            TimeToFinality(
                chain=ChainType.BASE,
                soft_finality_seconds=0.2,
            ),
            Decentralization(
                chain=ChainType.ETHEREUM,
                total_amount_of_stakes=32_000_000.0,
                number_of_nodes=1_000_000,
                nakamoto_liveness_coefficient=3,
                nakamoto_safety_coefficient=6,
                hhi=0.001,
                shapley_top_value=0.4,
                shapley_second_value=0.3,
                shapley_third_value=0.3,
                renyi_entropy_alpha_0=13.8,
                renyi_entropy_alpha_1=13.7,
                renyi_entropy_alpha_2=13.6,
                renyi_entropy_alpha_inf=6.9,
            ),
        ]
    )
    await session.commit()


async def test_metrics_unauthenticated(client: AsyncClient) -> None:
    """All metrics endpoints require authentication and return 401."""
    for path in _METRICS_PATHS:
        response = await client.get(path)
        assert response.status_code == 401, (
            f"Expected 401 for {path}, got {response.status_code}"
        )


async def test_metrics_chains_list(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """Chains endpoint returns all ChainType members."""
    response = await client.get("/metrics/chains", headers=user_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert set(data["chains"]) == {
        "ARBITRUM",
        "ETHEREUM",
        "SOLANA",
        "BASE",
        "INK",
        "UNICHAIN",
        "POLYGON",
        "AVALANCHE_C",
        "OPTIMISM",
        "CANTON",
    }


async def test_metrics_ethereum_list(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """Ethereum finality list returns paginated snapshots with correct fields."""
    response = await client.get(
        "/metrics/finality/ethereum", headers=user_token_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["headHeight"] == 20_000_000
    assert item["finalizedHeight"] == 19_999_936
    assert item["safeHeight"] == 19_999_968
    assert item["justifiedEpoch"] == 625_000
    assert item["finalizedEpoch"] == 624_999
    assert item["justifiedFinalizedGap"] == 1
    assert item["headToFinalizedTime"] == 768
    assert "id" in item
    assert "createdAt" in item


async def test_metrics_evm_l2_list(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """EVM L2 finality list returns all chains."""
    response = await client.get("/metrics/finality/evm-l2", headers=user_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    chains = {item["chain"] for item in data["items"]}
    assert chains == {"ARBITRUM", "BASE"}


async def test_metrics_evm_l2_arbitrum_fields(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """Arbitrum snapshot includes hard-finality fields."""
    response = await client.get("/metrics/finality/evm-l2", headers=user_token_headers)
    assert response.status_code == 200
    items = response.json()["items"]
    [item] = [i for i in items if i["chain"] == "ARBITRUM"]
    assert item["latestHeight"] == 250_000_000
    assert item["heightCorrelation"] == 20
    assert item["timeToHardFinality"] == 1200


async def test_metrics_evm_l2_base_fields(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """Base snapshot has null hard-finality fields."""
    response = await client.get("/metrics/finality/evm-l2", headers=user_token_headers)
    assert response.status_code == 200
    items = response.json()["items"]
    [item] = [i for i in items if i["chain"] == "BASE"]
    assert item["heightCorrelation"] is None
    assert item["timeToHardFinality"] is None


async def test_metrics_op_stack_list(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """OP Stack finality list returns all chains."""
    response = await client.get(
        "/metrics/finality/op-stack", headers=user_token_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    chains = {item["chain"] for item in data["items"]}
    assert chains == {"INK", "UNICHAIN"}


async def test_metrics_op_stack_ink_fields(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """INK snapshot has correct block heights."""
    response = await client.get(
        "/metrics/finality/op-stack", headers=user_token_headers
    )
    assert response.status_code == 200
    items = response.json()["items"]
    [item] = [i for i in items if i["chain"] == "INK"]
    assert item["unsafeHeight"] == 5_000_000
    assert item["safeHeight"] == 4_999_900
    assert item["finalizedHeight"] == 4_999_800


async def test_metrics_polygon_list(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """Polygon finality list returns snapshot with correct fields."""
    response = await client.get("/metrics/finality/polygon", headers=user_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["latestHeight"] == 60_000_000
    assert item["finalizedHeight"] == 59_999_900
    assert item["latestToFinalizedBlocks"] == 100
    assert "id" in item
    assert "createdAt" in item


async def test_metrics_solana_list(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """Solana finality list returns snapshot with correct fields."""
    response = await client.get("/metrics/finality/solana", headers=user_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["processedSlot"] == 300_000_000
    assert item["confirmedSlot"] == 299_999_990
    assert item["finalizedSlot"] == 299_999_950
    assert item["confirmedFinalizedGap"] == 40
    assert item["processedConfirmedGap"] == 10


async def test_metrics_created_before_filter_includes(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """CreatedBefore with a far-future date returns all records."""
    response = await client.get(
        "/metrics/finality/ethereum",
        params={"createdBefore": "2099-01-01T00:00:00Z"},
        headers=user_token_headers,
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_metrics_created_before_filter_excludes(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """CreatedBefore with a past date returns no records."""
    response = await client.get(
        "/metrics/finality/ethereum",
        params={"createdBefore": "2000-01-01T00:00:00Z"},
        headers=user_token_headers,
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_metrics_created_after_filter_includes(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """CreatedAfter with a past date returns all records."""
    response = await client.get(
        "/metrics/finality/ethereum",
        params={"createdAfter": "2000-01-01T00:00:00Z"},
        headers=user_token_headers,
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_metrics_pagination(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """Pagination limits the number of returned items."""
    response = await client.get(
        "/metrics/finality/evm-l2",
        params={"pageSize": 1, "currentPage": 1},
        headers=user_token_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 1


async def test_metrics_throughput_list(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """Throughput endpoint returns seeded rows with camelCased fields."""
    response = await client.get("/metrics/throughput", headers=user_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    chains = {item["chain"] for item in data["items"]}
    assert chains == {"ARBITRUM", "SOLANA"}
    [arb] = [i for i in data["items"] if i["chain"] == "ARBITRUM"]
    assert arb["gasPrice"] == 0.1
    assert arb["transactionsPerSecond"] == 20.0
    assert arb["blocksPerSecond"] == 4.0


async def test_metrics_throughput_filter_by_chain(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """``chain`` query filters the throughput list."""
    response = await client.get(
        "/metrics/throughput", params={"chain": "SOLANA"}, headers=user_token_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["chain"] == "SOLANA"


async def test_metrics_time_to_finality_list(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """Time-to-finality endpoint returns seeded rows."""
    response = await client.get("/metrics/time-to-finality", headers=user_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    chains = {item["chain"] for item in data["items"]}
    assert chains == {"ETHEREUM", "BASE"}
    [eth] = [i for i in data["items"] if i["chain"] == "ETHEREUM"]
    assert eth["softFinalitySeconds"] == 12.1


async def test_metrics_time_to_finality_filter_by_chain(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """``chain`` query filters the time-to-finality list."""
    response = await client.get(
        "/metrics/time-to-finality",
        params={"chain": "BASE"},
        headers=user_token_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["chain"] == "BASE"
    assert data["items"][0]["softFinalitySeconds"] == 0.2


async def test_metrics_decentralization_list(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """Decentralization endpoint exposes all 12 combined fields."""
    response = await client.get("/metrics/decentralization", headers=user_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["chain"] == "ETHEREUM"
    assert item["numberOfNodes"] == 1_000_000
    assert item["nakamotoLivenessCoefficient"] == 3
    assert item["nakamotoSafetyCoefficient"] == 6
    assert item["hhi"] == 0.001
    assert item["shapleyTopValue"] == 0.4
    assert item["renyiEntropyAlphaInf"] == 6.9


async def test_metrics_decentralization_filter_by_unseeded_chain(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """Filtering by a chain with no seeded data returns an empty list."""
    response = await client.get(
        "/metrics/decentralization",
        params={"chain": "SOLANA"},
        headers=user_token_headers,
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0
