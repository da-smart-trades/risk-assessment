# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from advanced_alchemy.extensions.litestar.providers import (
    FilterConfig,
    create_service_dependencies,
)
from advanced_alchemy.filters import CollectionFilter
from litestar import Controller, get
from litestar.params import Dependency

from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.metrics.schemas import (
    ChainList,
    Decentralization,
    DecentralizationCanton,
    FinalityCanton,
    FinalityEthereum,
    FinalityEvmL2,
    FinalityOpStack,
    FinalityPolygon,
    FinalitySolana,
    Governance,
    OperatorSnapshot,
    Throughput,
    TimeToFinality,
    TokenActivitySnapshot,
    TokenList,
)
from cert_ra.api.domain.metrics.services import (
    DecentralizationCantonService,
    DecentralizationOperatorSnapshotService,
    DecentralizationService,
    FinalityCantonService,
    FinalityEthereumService,
    FinalityEvmL2Service,
    FinalityOpStackService,
    FinalityPolygonService,
    FinalitySolanaService,
    GovernanceService,
    ThroughputService,
    TimeToFinalityService,
    TokenActivityService,
)
from cert_ra.types import ChainType, TokenType

if TYPE_CHECKING:
    from advanced_alchemy.filters import FilterTypes
    from advanced_alchemy.service import OffsetPagination

_BASE_FILTERS: FilterConfig = {
    "id_filter": UUID,
    "created_at": True,
    "sort_field": "created_at",
    "sort_order": "desc",
    "pagination_type": "limit_offset",
    "pagination_size": 50,
}


class MetricsController(Controller):
    """Blockchain metrics — read-only paginated snapshots."""

    path = "/metrics"
    guards = [requires_active_user]  # noqa: RUF012
    tags = ["Metrics"]  # noqa: RUF012
    signature_namespace = {  # noqa: RUF012
        "DecentralizationCantonService": DecentralizationCantonService,
        "DecentralizationOperatorSnapshotService": DecentralizationOperatorSnapshotService,
        "DecentralizationService": DecentralizationService,
        "FinalityCantonService": FinalityCantonService,
        "FinalityEthereumService": FinalityEthereumService,
        "FinalityEvmL2Service": FinalityEvmL2Service,
        "FinalityOpStackService": FinalityOpStackService,
        "FinalityPolygonService": FinalityPolygonService,
        "FinalitySolanaService": FinalitySolanaService,
        "GovernanceService": GovernanceService,
        "ThroughputService": ThroughputService,
        "TimeToFinalityService": TimeToFinalityService,
        "TokenActivityService": TokenActivityService,
    }

    @get(
        "/chains",
        operation_id="ListChains",
        name="metrics:chains",
        summary="List all available chains",
    )
    async def list_chains(self) -> ChainList:
        """List all available chains.

        Returns:
            All chains supported by the platform.
        """
        return ChainList(chains=list(ChainType))

    @get(
        "/tokens",
        operation_id="ListTokens",
        name="metrics:tokens",
        summary="List all available tokens",
    )
    async def list_tokens(self) -> TokenList:
        """List all available tokens.

        Returns:
            All tokens supported by the platform.
        """
        return TokenList(tokens=list(TokenType))

    @get(
        "/finality/ethereum",
        operation_id="ListFinalityEthereum",
        name="metrics:finality:ethereum",
        summary="List Ethereum finality snapshots",
        dependencies=create_service_dependencies(
            FinalityEthereumService, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_ethereum_finality(
        self,
        service: FinalityEthereumService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> OffsetPagination[FinalityEthereum]:
        """List Ethereum finality snapshots.

        Returns:
            Paginated list of Ethereum finality snapshots.
        """
        results, total = await service.list_and_count(*filters)
        return service.to_schema(
            schema_type=FinalityEthereum, data=results, total=total, filters=filters
        )

    @get(
        "/finality/evm-l2",
        operation_id="ListFinalityEvmL2",
        name="metrics:finality:evm-l2",
        summary="List EVM L2 finality snapshots",
        dependencies=create_service_dependencies(
            FinalityEvmL2Service, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_evm_l2_finality(
        self,
        service: FinalityEvmL2Service,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        chain: ChainType | None = None,
    ) -> OffsetPagination[FinalityEvmL2]:
        """List EVM L2 finality snapshots (Arbitrum / Base).

        Args:
            service: Injected EVM L2 finality service.
            filters: Injected pagination and sorting filters.
            chain: Optional chain filter (ARBITRUM or BASE).

        Returns:
            Paginated list of EVM L2 finality snapshots.
        """
        all_filters: list[FilterTypes] = (
            [*filters, CollectionFilter("chain", [chain])] if chain else list(filters)
        )
        results, total = await service.list_and_count(*all_filters)
        return service.to_schema(
            schema_type=FinalityEvmL2, data=results, total=total, filters=all_filters
        )

    @get(
        "/finality/op-stack",
        operation_id="ListFinalityOpStack",
        name="metrics:finality:op-stack",
        summary="List OP Stack finality snapshots",
        dependencies=create_service_dependencies(
            FinalityOpStackService, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_op_stack_finality(
        self,
        service: FinalityOpStackService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        chain: ChainType | None = None,
    ) -> OffsetPagination[FinalityOpStack]:
        """List OP Stack finality snapshots (Ink / Unichain).

        Args:
            service: Injected OP Stack finality service.
            filters: Injected pagination and sorting filters.
            chain: Optional chain filter (INK or UNICHAIN).

        Returns:
            Paginated list of OP Stack finality snapshots.
        """
        all_filters: list[FilterTypes] = (
            [*filters, CollectionFilter("chain", [chain])] if chain else list(filters)
        )
        results, total = await service.list_and_count(*all_filters)
        return service.to_schema(
            schema_type=FinalityOpStack, data=results, total=total, filters=all_filters
        )

    @get(
        "/finality/polygon",
        operation_id="ListFinalityPolygon",
        name="metrics:finality:polygon",
        summary="List Polygon finality snapshots",
        dependencies=create_service_dependencies(
            FinalityPolygonService, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_polygon_finality(
        self,
        service: FinalityPolygonService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> OffsetPagination[FinalityPolygon]:
        """List Polygon finality snapshots.

        Returns:
            Paginated list of Polygon finality snapshots.
        """
        results, total = await service.list_and_count(*filters)
        return service.to_schema(
            schema_type=FinalityPolygon, data=results, total=total, filters=filters
        )

    @get(
        "/finality/solana",
        operation_id="ListFinalitySolana",
        name="metrics:finality:solana",
        summary="List Solana finality snapshots",
        dependencies=create_service_dependencies(
            FinalitySolanaService, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_solana_finality(
        self,
        service: FinalitySolanaService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> OffsetPagination[FinalitySolana]:
        """List Solana finality snapshots.

        Returns:
            Paginated list of Solana finality snapshots.
        """
        results, total = await service.list_and_count(*filters)
        return service.to_schema(
            schema_type=FinalitySolana, data=results, total=total, filters=filters
        )

    @get(
        "/finality/canton",
        operation_id="ListFinalityCanton",
        name="metrics:finality:canton",
        summary="List Canton finality snapshots",
        dependencies=create_service_dependencies(
            FinalityCantonService, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_canton_finality(
        self,
        service: FinalityCantonService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> OffsetPagination[FinalityCanton]:
        """List combined Canton finality snapshots (round cadence + BFT quorum).

        Returns:
            Paginated list of Canton finality snapshots.
        """
        results, total = await service.list_and_count(*filters)
        return service.to_schema(
            schema_type=FinalityCanton, data=results, total=total, filters=filters
        )

    @get(
        "/throughput",
        operation_id="ListThroughput",
        name="metrics:throughput",
        summary="List throughput snapshots",
        dependencies=create_service_dependencies(
            ThroughputService, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_throughput(
        self,
        service: ThroughputService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        chain: ChainType | None = None,
    ) -> OffsetPagination[Throughput]:
        """List throughput snapshots (gas price, TPS, BPS).

        Args:
            service: Injected throughput service.
            filters: Injected pagination and sorting filters.
            chain: Optional chain filter.

        Returns:
            Paginated list of throughput snapshots.
        """
        all_filters: list[FilterTypes] = (
            [*filters, CollectionFilter("chain", [chain])] if chain else list(filters)
        )
        results, total = await service.list_and_count(*all_filters)
        return service.to_schema(
            schema_type=Throughput, data=results, total=total, filters=all_filters
        )

    @get(
        "/time-to-finality",
        operation_id="ListTimeToFinality",
        name="metrics:time-to-finality",
        summary="List soft time-to-finality snapshots",
        dependencies=create_service_dependencies(
            TimeToFinalityService, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_time_to_finality(
        self,
        service: TimeToFinalityService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        chain: ChainType | None = None,
    ) -> OffsetPagination[TimeToFinality]:
        """List soft time-to-finality snapshots.

        Args:
            service: Injected time-to-finality service.
            filters: Injected pagination and sorting filters.
            chain: Optional chain filter.

        Returns:
            Paginated list of time-to-finality snapshots.
        """
        all_filters: list[FilterTypes] = (
            [*filters, CollectionFilter("chain", [chain])] if chain else list(filters)
        )
        results, total = await service.list_and_count(*all_filters)
        return service.to_schema(
            schema_type=TimeToFinality,
            data=results,
            total=total,
            filters=all_filters,
        )

    @get(
        "/decentralization",
        operation_id="ListDecentralization",
        name="metrics:decentralization",
        summary="List decentralization snapshots",
        dependencies=create_service_dependencies(
            DecentralizationService, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_decentralization(
        self,
        service: DecentralizationService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        chain: ChainType | None = None,
    ) -> OffsetPagination[Decentralization]:
        """List decentralization snapshots (all 12 combined metrics).

        Args:
            service: Injected decentralization service.
            filters: Injected pagination and sorting filters.
            chain: Optional chain filter.

        Returns:
            Paginated list of decentralization snapshots.
        """
        all_filters: list[FilterTypes] = (
            [*filters, CollectionFilter("chain", [chain])] if chain else list(filters)
        )
        results, total = await service.list_and_count(*all_filters)
        return service.to_schema(
            schema_type=Decentralization,
            data=results,
            total=total,
            filters=all_filters,
        )

    @get(
        "/decentralization/canton",
        operation_id="ListDecentralizationCanton",
        name="metrics:decentralization:canton",
        summary="List Canton Super-Validator decentralization snapshots",
        dependencies=create_service_dependencies(
            DecentralizationCantonService, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_canton_decentralization(
        self,
        service: DecentralizationCantonService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> OffsetPagination[DecentralizationCanton]:
        """List Canton governance-decentralization snapshots (gov Nakamoto).

        Returns:
            Paginated list of Canton decentralization snapshots.
        """
        results, total = await service.list_and_count(*filters)
        return service.to_schema(
            schema_type=DecentralizationCanton,
            data=results,
            total=total,
            filters=filters,
        )

    @get(
        "/decentralization/operators",
        operation_id="ListOperatorSnapshots",
        name="metrics:operator-snapshots",
        summary="List top-operator snapshots",
        dependencies=create_service_dependencies(
            DecentralizationOperatorSnapshotService,
            key="service",
            filters=_BASE_FILTERS,
        ),
    )
    async def list_operator_snapshots(
        self,
        service: DecentralizationOperatorSnapshotService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        chain: ChainType | None = None,
    ) -> OffsetPagination[OperatorSnapshot]:
        """Top staking operators (entity-level decentralization) per chain.

        Defaults sort newest-first, so a ``size=1`` request returns the latest
        snapshot. Currently only Ethereum is populated (via Rated Network).

        Args:
            service: Injected operator snapshot service.
            filters: Injected pagination and sorting filters.
            chain: Optional chain filter.

        Returns:
            Paginated list of operator snapshots.
        """
        all_filters: list[FilterTypes] = (
            [*filters, CollectionFilter("chain", [chain])] if chain else list(filters)
        )
        results, total = await service.list_and_count(*all_filters)
        return service.to_schema(
            schema_type=OperatorSnapshot,
            data=results,
            total=total,
            filters=all_filters,
        )

    @get(
        "/token-activity",
        operation_id="ListTokenActivity",
        name="metrics:token-activity",
        summary="List token activity snapshots",
        dependencies=create_service_dependencies(
            TokenActivityService, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_token_activity(
        self,
        service: TokenActivityService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        token: TokenType | None = None,
        chain: ChainType | None = None,
    ) -> OffsetPagination[TokenActivitySnapshot]:
        """List token activity snapshots (inflow, outflow, supply, etc.).

        Args:
            service: Injected token activity service.
            filters: Injected pagination and sorting filters.
            token: Optional token filter.
            chain: Optional chain filter.

        Returns:
            Paginated list of token activity snapshots.
        """
        all_filters: list[FilterTypes] = list(filters)
        if token:
            all_filters.append(CollectionFilter("token", [token]))
        if chain:
            all_filters.append(CollectionFilter("chain", [chain]))
        results, total = await service.list_and_count(*all_filters)
        return service.to_schema(
            schema_type=TokenActivitySnapshot,
            data=results,
            total=total,
            filters=all_filters,
        )

    @get(
        "/governance",
        operation_id="ListGovernance",
        name="metrics:governance",
        summary="List governance event snapshots",
        dependencies=create_service_dependencies(
            GovernanceService, key="service", filters=_BASE_FILTERS
        ),
    )
    async def list_governance(
        self,
        service: GovernanceService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        chain: ChainType | None = None,
        event_type: str | None = None,
    ) -> OffsetPagination[Governance]:
        """List governance event snapshots.

        Args:
            service: Injected governance service.
            filters: Injected pagination and sorting filters.
            chain: Optional chain filter.
            event_type: Optional event-type filter
                (``"proposals"``, ``"execution"``, or ``"emergency"``).

        Returns:
            Paginated list of governance event snapshots.
        """
        all_filters: list[FilterTypes] = list(filters)
        if chain:
            all_filters.append(CollectionFilter("chain", [chain]))
        if event_type:
            all_filters.append(CollectionFilter("event_type", [event_type]))
        results, total = await service.list_and_count(*all_filters)
        return service.to_schema(
            schema_type=Governance,
            data=results,
            total=total,
            filters=all_filters,
        )
