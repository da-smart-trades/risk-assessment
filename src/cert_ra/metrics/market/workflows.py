# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Temporal workflows that drive the market collector and scorer.

Two workflows, both fanned out by Temporal Schedules in
:mod:`cert_ra.metrics.worker`:

* :class:`MarketCollectorWorkflow` — every 5 minutes. Loads the
  enabled protocols (operator-curated ``market_config`` rows), asks
  yarn for the live ``(chainId, marketId, label)`` set per protocol,
  then dispatches ``collect_market_snapshot`` activities in
  **parallel batches** of ``concurrency`` markets at a time.
* :class:`MarketScorerWorkflow` — every hour, offset 60 s past the
  collector cycle so the scorer reads fresh metrics. Same fan-out shape.

Batched fan-out (rather than one-at-a-time or all-at-once) keeps the work
deterministic — which workflow code must be — while turning an ``N × T`` tick
into ``⌈N / concurrency⌉ × T``. The batch size is bounded on purpose: the
collect/score activities shell out to an LLM, so unbounded fan-out would just
trade latency for rate-limit errors and cost. ``concurrency`` comes from
``TemporalSettings.market_fanout_concurrency`` via the schedule argument.

The per-protocol yarn list call runs as its own activity so a flaky yarn
discovery for one protocol doesn't sink the tick for the others; a
list-failure is logged and that protocol is skipped for the cycle. A
non-retryable :exc:`MarketSnapshotPayloadError` from the per-market
activity layer drops just that market for the tick; we keep going for
the others.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from cert_ra.metrics.market import activities
    from cert_ra.metrics.market.schemas import (  # noqa: TC001
        MarketConfigRef,
        MarketTickRef,
    )

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


# Fallback batch size for schedules created before the concurrency argument
# existed (they start the workflow with no argument). Mirrors the default of
# ``TemporalSettings.market_fanout_concurrency``.
_DEFAULT_FANOUT_CONCURRENCY = 6

# ---------------------------------------------------------------------------
# Retry policies
# ---------------------------------------------------------------------------

_LOAD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)

_LIST_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    # A malformed listing or rejected protocol slug isn't going to fix
    # itself between attempts — surface them straight to the workflow.
    non_retryable_error_types=[
        "MarketSnapshotPayloadError",
        "YarnInputError",
    ],
)

_YARN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
    # MarketSnapshotPayloadError signals "the LLM emitted bad JSON" — retrying
    # without a code or prompt change just wastes credits. YarnInputError
    # signals an admin entered a bad protocol/hex; same logic.
    non_retryable_error_types=[
        "MarketSnapshotPayloadError",
        "YarnInputError",
    ],
)

_LOAD_TIMEOUT = timedelta(seconds=30)
_LIST_TIMEOUT = timedelta(minutes=2)
_YARN_TIMEOUT = timedelta(minutes=2)


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


def _batches[T](items: list[T], size: int) -> Iterator[list[T]]:
    """Yield successive ``size``-length slices of ``items`` (size coerced ≥ 1)."""
    size = max(1, size)
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def _list_markets_for_protocols(
    protocols: list[MarketConfigRef],
) -> list[MarketTickRef]:
    """Resolve every enabled protocol into the markets it currently exposes.

    Runs the per-protocol ``list_protocol_markets`` activities serially —
    the workflow already fans out at the per-market level afterwards, and
    yarn list calls are cheap enough that overlapping them doesn't buy
    much. A failure for one protocol is logged and skipped so a flaky
    yarn discovery doesn't sink the tick for the others.
    """
    refs: list[MarketTickRef] = []
    for protocol in protocols:
        try:
            chunk: list[MarketTickRef] = await workflow.execute_activity(
                activities.list_protocol_markets,
                protocol,
                start_to_close_timeout=_LIST_TIMEOUT,
                retry_policy=_LIST_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — keep going for sibling protocols
            workflow.logger.warning(
                "list_protocol_markets failed for %s: %s",
                protocol.protocol,
                exc,
            )
            continue
        refs.extend(chunk)
    return refs


async def _fan_out(
    refs: list[MarketTickRef],
    activity: Callable[..., object],
    label: str,
    concurrency: int,
) -> None:
    """Run ``activity`` for every market, ``concurrency`` markets at a time.

    Each batch is dispatched with :func:`asyncio.gather`, so up to
    ``concurrency`` activities are in flight at once; the next batch starts
    only after the current one drains. A single market's failure is logged and
    skipped — it never aborts the batch or the tick (mirrors the original
    per-market ``try/except``); non-``Exception`` failures (e.g. workflow
    cancellation) still propagate.
    """

    async def _run_one(ref: MarketTickRef) -> None:
        try:
            await workflow.execute_activity(
                activity,
                ref,
                start_to_close_timeout=_YARN_TIMEOUT,
                retry_policy=_YARN_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — keep going for sibling markets
            workflow.logger.warning(
                "%s failed for %s/%s/%s: %s",
                label,
                ref.protocol,
                ref.chain_id,
                ref.market_id_hex,
                exc,
            )

    for batch in _batches(refs, concurrency):
        await asyncio.gather(*(_run_one(ref) for ref in batch))


@workflow.defn
class MarketCollectorWorkflow:
    """Per-tick collector: load protocols, list markets, then collect in batches."""

    @workflow.run
    async def run(self, concurrency: int = _DEFAULT_FANOUT_CONCURRENCY) -> None:
        """Read enabled protocols, list their markets via yarn, then fan out.

        A per-market activity failure does not abort the tick — sibling
        markets still get their chance. Failures surface in Temporal's
        per-activity history and via the activity's exception, which the
        worker logs.
        """
        protocols: list[MarketConfigRef] = await workflow.execute_activity(
            activities.load_enabled_protocols,
            start_to_close_timeout=_LOAD_TIMEOUT,
            retry_policy=_LOAD_RETRY,
        )
        refs = await _list_markets_for_protocols(protocols)
        await _fan_out(
            refs,
            activities.collect_market_snapshot,
            "collect_market_snapshot",
            concurrency,
        )


@workflow.defn
class MarketScorerWorkflow:
    """Per-tick scorer: same fan-out shape as the collector, with --score."""

    @workflow.run
    async def run(self, concurrency: int = _DEFAULT_FANOUT_CONCURRENCY) -> None:
        """Read enabled protocols, list their markets via yarn, then score."""
        protocols: list[MarketConfigRef] = await workflow.execute_activity(
            activities.load_enabled_protocols,
            start_to_close_timeout=_LOAD_TIMEOUT,
            retry_policy=_LOAD_RETRY,
        )
        refs = await _list_markets_for_protocols(protocols)
        await _fan_out(
            refs,
            activities.score_market_snapshot,
            "score_market_snapshot",
            concurrency,
        )
