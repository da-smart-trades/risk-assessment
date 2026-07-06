# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import SQLAlchemyAsyncRepositoryService

from cert_ra.db.models import (
    Decentralization,
    DecentralizationCanton,
    DecentralizationOperatorSnapshot,
    GovernanceEvent,
    Throughput,
    TimeToFinality,
    TokenActivity,
)
from cert_ra.db.models.finality import (
    FinalityCanton,
    FinalityEthereum,
    FinalityEvmL2,
    FinalityOpStack,
    FinalityPolygon,
    FinalitySolana,
)

__all__ = (
    "DecentralizationCantonService",
    "DecentralizationOperatorSnapshotService",
    "DecentralizationService",
    "FinalityCantonService",
    "FinalityEthereumService",
    "FinalityEvmL2Service",
    "FinalityOpStackService",
    "FinalityPolygonService",
    "FinalitySolanaService",
    "GovernanceService",
    "ThroughputService",
    "TimeToFinalityService",
    "TokenActivityService",
)


class FinalityEthereumService(SQLAlchemyAsyncRepositoryService[FinalityEthereum]):
    """Read-only service for Ethereum finality snapshots."""

    class Repo(SQLAlchemyAsyncRepository[FinalityEthereum]):
        """Ethereum finality repository."""

        model_type = FinalityEthereum

    repository_type = Repo


class FinalityEvmL2Service(SQLAlchemyAsyncRepositoryService[FinalityEvmL2]):
    """Read-only service for EVM L2 finality snapshots (Arbitrum / Base)."""

    class Repo(SQLAlchemyAsyncRepository[FinalityEvmL2]):
        """EVM L2 finality repository."""

        model_type = FinalityEvmL2

    repository_type = Repo


class FinalityOpStackService(SQLAlchemyAsyncRepositoryService[FinalityOpStack]):
    """Read-only service for OP Stack finality snapshots (Ink / Unichain)."""

    class Repo(SQLAlchemyAsyncRepository[FinalityOpStack]):
        """OP Stack finality repository."""

        model_type = FinalityOpStack

    repository_type = Repo


class FinalityPolygonService(SQLAlchemyAsyncRepositoryService[FinalityPolygon]):
    """Read-only service for Polygon finality snapshots."""

    class Repo(SQLAlchemyAsyncRepository[FinalityPolygon]):
        """Polygon finality repository."""

        model_type = FinalityPolygon

    repository_type = Repo


class FinalitySolanaService(SQLAlchemyAsyncRepositoryService[FinalitySolana]):
    """Read-only service for Solana finality snapshots."""

    class Repo(SQLAlchemyAsyncRepository[FinalitySolana]):
        """Solana finality repository."""

        model_type = FinalitySolana

    repository_type = Repo


class FinalityCantonService(SQLAlchemyAsyncRepositoryService[FinalityCanton]):
    """Read-only service for Canton finality snapshots."""

    class Repo(SQLAlchemyAsyncRepository[FinalityCanton]):
        """Canton finality repository."""

        model_type = FinalityCanton

    repository_type = Repo


class ThroughputService(SQLAlchemyAsyncRepositoryService[Throughput]):
    """Read-only service for throughput snapshots (gas price, TPS, BPS)."""

    class Repo(SQLAlchemyAsyncRepository[Throughput]):
        """Throughput repository."""

        model_type = Throughput

    repository_type = Repo


class TimeToFinalityService(SQLAlchemyAsyncRepositoryService[TimeToFinality]):
    """Read-only service for soft time-to-finality snapshots."""

    class Repo(SQLAlchemyAsyncRepository[TimeToFinality]):
        """Time-to-finality repository."""

        model_type = TimeToFinality

    repository_type = Repo


class DecentralizationService(SQLAlchemyAsyncRepositoryService[Decentralization]):
    """Read-only service for decentralization snapshots."""

    class Repo(SQLAlchemyAsyncRepository[Decentralization]):
        """Decentralization repository."""

        model_type = Decentralization

    repository_type = Repo


class DecentralizationCantonService(
    SQLAlchemyAsyncRepositoryService[DecentralizationCanton]
):
    """Read-only service for Canton Super-Validator decentralization snapshots."""

    class Repo(SQLAlchemyAsyncRepository[DecentralizationCanton]):
        """Canton decentralization repository."""

        model_type = DecentralizationCanton

    repository_type = Repo


class DecentralizationOperatorSnapshotService(
    SQLAlchemyAsyncRepositoryService[DecentralizationOperatorSnapshot]
):
    """Read-only service for top-operator snapshots."""

    class Repo(SQLAlchemyAsyncRepository[DecentralizationOperatorSnapshot]):
        """Operator snapshot repository."""

        model_type = DecentralizationOperatorSnapshot

    repository_type = Repo


class TokenActivityService(SQLAlchemyAsyncRepositoryService[TokenActivity]):
    """Read-only service for token activity snapshots."""

    class Repo(SQLAlchemyAsyncRepository[TokenActivity]):
        """Token activity repository."""

        model_type = TokenActivity

    repository_type = Repo


class GovernanceService(SQLAlchemyAsyncRepositoryService[GovernanceEvent]):
    """Read-only service for governance event snapshots."""

    class Repo(SQLAlchemyAsyncRepository[GovernanceEvent]):
        """Governance event repository."""

        model_type = GovernanceEvent

    repository_type = Repo
