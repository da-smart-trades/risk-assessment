# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Temporal worker for metrics collection.

Start with::

    uv run python -m cert_ra.metrics.worker

or register it as a CLI entry point and run ``certora-risk-worker``.

On first startup the worker creates one Temporal schedule per
(metric-group, chain) pair. Subsequent startups reconcile the interval of any
existing schedule whose cadence has drifted from the values defined here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.service import RPCError
from temporalio.worker import Worker

from cert_ra.metrics.canton.activities import (
    fetch_canton_decentralization,
    fetch_canton_finality,
    store_canton_decentralization,
    store_canton_finality,
)
from cert_ra.metrics.canton.workflows import (
    CantonDecentralizationWorkflow,
    CantonFinalityWorkflow,
)
from cert_ra.metrics.decentralization.activities import (
    fetch_decentralization,
    fetch_operator_snapshot,
    store_decentralization,
    store_operator_snapshot,
)
from cert_ra.metrics.decentralization.schemas import (
    DecentralizationParams,
    OperatorSnapshotParams,
)
from cert_ra.metrics.decentralization.workflows import (
    DecentralizationWorkflow,
    OperatorSnapshotWorkflow,
)
from cert_ra.metrics.finality.activities import (
    fetch_ethereum_finality,
    fetch_evm_l2_finality,
    fetch_op_stack_finality,
    fetch_polygon_finality,
    fetch_solana_finality,
    store_ethereum_finality,
    store_evm_l2_finality,
    store_op_stack_finality,
    store_polygon_finality,
    store_solana_finality,
)
from cert_ra.metrics.finality.schemas import ChainParams
from cert_ra.metrics.finality.workflows import (
    EthereumFinalityWorkflow,
    EvmL2FinalityWorkflow,
    OPStackFinalityWorkflow,
    PolygonFinalityWorkflow,
    SolanaFinalityWorkflow,
)
from cert_ra.metrics.governance.activities import fetch_governance, store_governance
from cert_ra.metrics.governance.schemas import GovernanceParams
from cert_ra.metrics.governance.workflows import GovernanceWorkflow
from cert_ra.metrics.market.activities import (
    collect_market_snapshot,
    list_protocol_markets,
    load_enabled_protocols,
    score_market_snapshot,
)
from cert_ra.metrics.market.workflows import (
    MarketCollectorWorkflow,
    MarketScorerWorkflow,
)
from cert_ra.metrics.throughput.activities import fetch_throughput, store_throughput
from cert_ra.metrics.throughput.schemas import ThroughputParams
from cert_ra.metrics.throughput.workflows import ThroughputWorkflow
from cert_ra.metrics.time_to_finality.activities import (
    fetch_time_to_finality,
    store_time_to_finality,
)
from cert_ra.metrics.time_to_finality.schemas import TimeToFinalityParams
from cert_ra.metrics.time_to_finality.workflows import TimeToFinalityWorkflow
from cert_ra.metrics.tokens.activities import fetch_token_activity, store_token_activity
from cert_ra.metrics.tokens.schemas import TokenActivityParams
from cert_ra.metrics.tokens.workflows import TokenActivityWorkflow
from cert_ra.metrics.tvl.activities import fetch_tvl, store_tvl
from cert_ra.metrics.tvl.schemas import TVLParams
from cert_ra.metrics.tvl.workflows import TVLWorkflow
from cert_ra.settings.temporal import get_temporal_settings
from cert_ra.temporal.client import connect_temporal

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

TASK_QUEUE = "metrics"

# Schedule intervals per metric group, chosen to match the old-setup
# ``update_freq`` values while avoiding unnecessary load on upstream APIs.
_FINALITY_INTERVAL = timedelta(seconds=30)
# Canton finality tracks round cadence (~10-min rounds) + ledger freshness +
# SV BFT quorum margin, which all move slowly, and each poll fans out across
# several Scan endpoints (against a rate-limited public proxy) — so it runs
# far less aggressively than the 30s chains.
_CANTON_FINALITY_INTERVAL = timedelta(minutes=5)
_THROUGHPUT_INTERVAL = timedelta(minutes=30)
_TIME_TO_FINALITY_INTERVAL = timedelta(minutes=10)
_DECENTRALIZATION_INTERVAL = timedelta(minutes=5)
# Canton's SV set / validator roster changes slowly and its fetch paginates the
# full validator-license list, so it polls less often than the stake chains.
_CANTON_DECENTRALIZATION_INTERVAL = timedelta(minutes=15)
# Rated operator data is rolled up daily upstream and the API is rate-limited
# on lower tiers, so we refresh once a day rather than every few minutes.
_OPERATOR_SNAPSHOT_INTERVAL = timedelta(hours=24)
_TVL_INTERVAL = timedelta(hours=1)
_TOKEN_ACTIVITY_INTERVAL = timedelta(hours=3)
_GOVERNANCE_INTERVAL = timedelta(hours=6)
_MARKET_COLLECTOR_INTERVAL = timedelta(minutes=5)
_MARKET_SCORER_INTERVAL = timedelta(hours=6)
# Run the scorer 60 seconds past the hour so it sees a fresh collector
# snapshot. Collector ticks fall on :00, :05, :10, ... so :60 = :00 + 60s
# is guaranteed to land after the most recent collector tick has had
# time to complete a typical yarn run.
_MARKET_SCORER_OFFSET = timedelta(seconds=60)


_WORKFLOWS: Sequence[Any] = [
    EthereumFinalityWorkflow,
    EvmL2FinalityWorkflow,
    OPStackFinalityWorkflow,
    PolygonFinalityWorkflow,
    SolanaFinalityWorkflow,
    CantonFinalityWorkflow,
    CantonDecentralizationWorkflow,
    ThroughputWorkflow,
    TimeToFinalityWorkflow,
    DecentralizationWorkflow,
    OperatorSnapshotWorkflow,
    TVLWorkflow,
    TokenActivityWorkflow,
    GovernanceWorkflow,
    MarketCollectorWorkflow,
    MarketScorerWorkflow,
]

_ACTIVITIES: Sequence[Callable[..., Any]] = [
    fetch_ethereum_finality,
    fetch_evm_l2_finality,
    fetch_op_stack_finality,
    fetch_polygon_finality,
    fetch_solana_finality,
    store_ethereum_finality,
    store_evm_l2_finality,
    store_op_stack_finality,
    store_polygon_finality,
    store_solana_finality,
    fetch_canton_finality,
    store_canton_finality,
    fetch_canton_decentralization,
    store_canton_decentralization,
    fetch_throughput,
    store_throughput,
    fetch_time_to_finality,
    store_time_to_finality,
    fetch_decentralization,
    store_decentralization,
    fetch_operator_snapshot,
    store_operator_snapshot,
    fetch_tvl,
    store_tvl,
    fetch_token_activity,
    store_token_activity,
    fetch_governance,
    store_governance,
    load_enabled_protocols,
    list_protocol_markets,
    collect_market_snapshot,
    score_market_snapshot,
]


# Each entry: ``(schedule_id, workflow_class, optional_arg, interval)`` or
# the 5-tuple ``(schedule_id, workflow_class, optional_arg, interval, offset)``.
# The optional ``offset`` shifts the recurrence cycle so the scorer's
# ticks land 60 seconds past each collector tick.
_SCHEDULES: list[tuple] = [
    # --- Finality -----------------------------------------------------------
    ("finality-ethereum", EthereumFinalityWorkflow, None, _FINALITY_INTERVAL),
    (
        "finality-arbitrum",
        EvmL2FinalityWorkflow,
        ChainParams(chain="ARBITRUM"),
        _FINALITY_INTERVAL,
    ),
    (
        "finality-base",
        EvmL2FinalityWorkflow,
        ChainParams(chain="BASE"),
        _FINALITY_INTERVAL,
    ),
    (
        "finality-optimism",
        EvmL2FinalityWorkflow,
        ChainParams(chain="OPTIMISM"),
        _FINALITY_INTERVAL,
    ),
    (
        "finality-ink",
        OPStackFinalityWorkflow,
        ChainParams(chain="INK"),
        _FINALITY_INTERVAL,
    ),
    (
        "finality-unichain",
        OPStackFinalityWorkflow,
        ChainParams(chain="UNICHAIN"),
        _FINALITY_INTERVAL,
    ),
    ("finality-polygon", PolygonFinalityWorkflow, None, _FINALITY_INTERVAL),
    ("finality-solana", SolanaFinalityWorkflow, None, _FINALITY_INTERVAL),
    (
        "finality-canton",
        CantonFinalityWorkflow,
        None,
        _CANTON_FINALITY_INTERVAL,
    ),
    # --- Throughput (Dune ``*.transactions`` / Canton Scan API) -------------
    *[
        (
            f"throughput-{chain.lower()}",
            ThroughputWorkflow,
            ThroughputParams(chain=chain),
            _THROUGHPUT_INTERVAL,
        )
        for chain in (
            "ETHEREUM",
            "ARBITRUM",
            "SOLANA",
            "INK",
            "UNICHAIN",
            "POLYGON",
            "AVALANCHE_C",
            "OPTIMISM",
            "BASE",
            "CANTON",
        )
    ],
    # --- Time to finality (websocket subscriptions) -------------------------
    *[
        (
            f"time-to-finality-{chain.lower()}",
            TimeToFinalityWorkflow,
            TimeToFinalityParams(chain=chain),
            _TIME_TO_FINALITY_INTERVAL,
        )
        for chain in ("ETHEREUM", "BASE", "INK", "UNICHAIN", "SOLANA")
    ],
    # --- Decentralization (validator stake sample) --------------------------
    *[
        (
            f"decentralization-{chain.lower()}",
            DecentralizationWorkflow,
            DecentralizationParams(chain=chain),
            _DECENTRALIZATION_INTERVAL,
        )
        for chain in ("ETHEREUM", "SOLANA", "POLYGON", "AVALANCHE_C")
    ],
    # --- Canton decentralization (Super-Validator governance Nakamoto) ------
    (
        "decentralization-canton",
        CantonDecentralizationWorkflow,
        None,
        _CANTON_DECENTRALIZATION_INTERVAL,
    ),
    # --- Operator snapshot (Ethereum via Rated, others via native APIs +  -
    #     curated labels) --------------------------------------------------
    *[
        (
            f"operator-snapshot-{chain.lower()}",
            OperatorSnapshotWorkflow,
            OperatorSnapshotParams(chain=chain),
            _OPERATOR_SNAPSHOT_INTERVAL,
        )
        for chain in ("ETHEREUM", "SOLANA", "POLYGON", "AVALANCHE_C")
    ],
    # --- TVL (DefiLlama ``/v2/chains``) -------------------------------------
    *[
        (
            f"tvl-{chain.lower()}",
            TVLWorkflow,
            TVLParams(chain=chain),
            _TVL_INTERVAL,
        )
        for chain in (
            "ETHEREUM",
            "ARBITRUM",
            "BASE",
            "INK",
            "UNICHAIN",
            "POLYGON",
            "AVALANCHE_C",
            "OPTIMISM",
            "SOLANA",
        )
    ],
    # --- Token activity (Dune ``tokens.transfers`` / ``tokens_solana``) -----
    *[
        (
            f"token-activity-{chain.lower()}-{token.lower()}",
            TokenActivityWorkflow,
            TokenActivityParams(chain=chain, token=token),
            _TOKEN_ACTIVITY_INTERVAL,
        )
        for chain, token in (
            # USDC — all 9 chains
            ("ETHEREUM", "USDC"),
            ("ARBITRUM", "USDC"),
            ("BASE", "USDC"),
            ("INK", "USDC"),
            ("UNICHAIN", "USDC"),
            ("POLYGON", "USDC"),
            ("AVALANCHE_C", "USDC"),
            ("OPTIMISM", "USDC"),
            ("SOLANA", "USDC"),
            # USDT0 — LayerZero-bridged chains
            ("ETHEREUM", "USDT0"),
            ("INK", "USDT0"),
            ("UNICHAIN", "USDT0"),
            ("OPTIMISM", "USDT0"),
            ("POLYGON", "USDT0"),
            # Ethereum-only token risk tokens
            ("ETHEREUM", "WETH"),
            ("ETHEREUM", "USDE"),
            ("ETHEREUM", "AAVE"),
            ("ETHEREUM", "UNI"),
            ("ETHEREUM", "AUSDC"),
            ("ETHEREUM", "CUSDC"),
            ("ETHEREUM", "LINK"),
            ("ETHEREUM", "STETH"),
            ("ETHEREUM", "WSTETH"),
            ("ETHEREUM", "CBBTC"),
        )
    ],
    # --- Governance (per-chain proposal / execution / emergency feeds) ------
    *[
        (
            f"governance-{chain.lower()}-{event_type}",
            GovernanceWorkflow,
            GovernanceParams(chain=chain, event_type=event_type),
            _GOVERNANCE_INTERVAL,
        )
        for chain, event_type in (
            ("ETHEREUM", "confirmed_eips"),
            ("ETHEREUM", "last_call_eips"),
            ("ARBITRUM", "proposals"),
            ("ARBITRUM", "execution"),
            ("ARBITRUM", "emergency"),
            ("BASE", "execution"),
            ("SOLANA", "proposals"),
        )
    ],
    # --- Automated market metrics (yarn subprocess) -------------------------
    ("market-collector", MarketCollectorWorkflow, None, _MARKET_COLLECTOR_INTERVAL),
    (
        "market-scorer",
        MarketScorerWorkflow,
        None,
        _MARKET_SCORER_INTERVAL,
        _MARKET_SCORER_OFFSET,
    ),
]


# Length of the 5-tuple form ``(id, workflow, arg, interval, offset)``.
# Entries below this length omit the offset and run at the natural cycle.
_SCHEDULE_OFFSET_TUPLE_LEN = 5


async def _ensure_schedules(client: Client, market_concurrency: int) -> None:
    """Create per-chain schedules, reconciling intervals on existing ones.

    Args:
        client: Connected Temporal client.
        market_concurrency: Per-tick fan-out batch size handed to the market
            collector/scorer workflows as their run argument (the per-chain
            metric workflows already carry their own ``Params`` argument).
    """
    for entry in _SCHEDULES:
        schedule_id, workflow_cls, arg, interval = entry[:4]
        if arg is None and workflow_cls in (
            MarketCollectorWorkflow,
            MarketScorerWorkflow,
        ):
            arg = market_concurrency
        offset: timedelta | None = (
            entry[4] if len(entry) >= _SCHEDULE_OFFSET_TUPLE_LEN else None
        )
        interval_spec = (
            ScheduleIntervalSpec(every=interval, offset=offset)
            if offset is not None
            else ScheduleIntervalSpec(every=interval)
        )
        spec = ScheduleSpec(intervals=[interval_spec])
        action = (
            ScheduleActionStartWorkflow(
                workflow_cls.run,
                arg,
                id=schedule_id,
                task_queue=TASK_QUEUE,
            )
            if arg is not None
            else ScheduleActionStartWorkflow(
                workflow_cls.run,
                id=schedule_id,
                task_queue=TASK_QUEUE,
            )
        )
        try:
            await client.create_schedule(
                schedule_id,
                Schedule(action=action, spec=spec),
            )
            logger.info("Created schedule %s", schedule_id)
            continue
        except (RPCError, ScheduleAlreadyRunningError):
            pass

        await _reconcile_schedule_interval(client, schedule_id, spec, interval, offset)


async def _reconcile_schedule_interval(
    client: Client,
    schedule_id: str,
    spec: ScheduleSpec,
    interval: timedelta,
    offset: timedelta | None = None,
) -> None:
    """Update an existing schedule's spec if its interval/offset no longer matches."""
    handle = client.get_schedule_handle(schedule_id)
    description = await handle.describe()
    current_intervals = description.schedule.spec.intervals
    current_every = current_intervals[0].every if current_intervals else None
    current_offset = current_intervals[0].offset if current_intervals else None
    if current_every == interval and current_offset == offset:
        logger.debug(
            "Schedule %s already at interval %s offset %s, skipping",
            schedule_id,
            interval,
            offset,
        )
        return

    def _updater(update_input: ScheduleUpdateInput) -> ScheduleUpdate:
        new_schedule = update_input.description.schedule
        new_schedule.spec = spec
        return ScheduleUpdate(schedule=new_schedule)

    await handle.update(_updater)
    logger.info(
        "Reconciled schedule %s interval %s -> %s",
        schedule_id,
        current_every,
        interval,
    )


async def run_worker() -> None:
    """Connect to Temporal, register chain schedules, and start the worker."""
    settings = get_temporal_settings()

    client = await connect_temporal(settings)

    await _ensure_schedules(client, settings.market_fanout_concurrency)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=_WORKFLOWS,
        activities=_ACTIVITIES,
        max_concurrent_activities=settings.worker_max_concurrent_activities,
    )

    await worker.run()


def main() -> None:
    """Script entrypoint for the ``certora-risk-metrics-worker`` console script."""
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
