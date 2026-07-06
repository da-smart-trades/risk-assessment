# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unit tests for the market collector/scorer workflow fan-out.

These stay at the unit boundary: ``workflow.execute_activity`` and
``workflow.logger`` are patched, so no Temporal server or real activities are
involved. We exercise the batching helper and ``_fan_out`` directly.

What we cover:

* ``_batches`` partitions correctly and coerces a non-positive size to 1.
* ``_fan_out`` attempts every market and isolates a single market's failure
  (logs + keeps going) — the original per-market ``try/except`` semantics.
* ``_fan_out`` never runs more than ``concurrency`` activities at once.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from cert_ra.metrics.market.activities import collect_market_snapshot
from cert_ra.metrics.market.schemas import MarketTickRef
from cert_ra.metrics.market.workflows import _batches, _fan_out

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio

_EXEC_TARGET = "cert_ra.metrics.market.workflows.workflow.execute_activity"
_LOGGER_TARGET = "cert_ra.metrics.market.workflows.workflow.logger"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _ref(i: int, market_id_hex: str | None = None) -> MarketTickRef:
    return MarketTickRef(
        market_config_id=uuid4(),
        protocol="aave",
        chain_id=1,
        market_id_hex=market_id_hex or f"0x{i:040x}",
        label=f"Aave {i}",
    )


# ---------------------------------------------------------------------------
# _batches
# ---------------------------------------------------------------------------


def test_batches_partitions_evenly_and_remainder() -> None:
    assert list(_batches([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_batches_single_batch_when_size_exceeds_len() -> None:
    assert list(_batches([1, 2, 3], 10)) == [[1, 2, 3]]


def test_batches_empty() -> None:
    assert list(_batches([], 4)) == []


@pytest.mark.parametrize("bad_size", [0, -3])
def test_batches_coerces_nonpositive_size_to_one(bad_size: int) -> None:
    assert list(_batches([1, 2, 3], bad_size)) == [[1], [2], [3]]


# ---------------------------------------------------------------------------
# _fan_out
# ---------------------------------------------------------------------------


async def test_fan_out_runs_every_market_and_isolates_failure(
    mocker: MockerFixture,
) -> None:
    seen: list[str] = []

    async def fake_execute(_activity: object, ref: MarketTickRef, **_: object) -> None:
        seen.append(ref.market_id_hex)
        if ref.market_id_hex == "0xbad":
            msg = "boom"
            raise RuntimeError(msg)

    mocker.patch(_EXEC_TARGET, new=fake_execute)
    logger = mocker.patch(_LOGGER_TARGET)

    refs = [_ref(0), _ref(1, "0xbad"), _ref(2)]
    # Must not raise despite the failing market.
    await _fan_out(refs, collect_market_snapshot, "collect_market_snapshot", 4)

    # Every market was attempted; the failure was logged, siblings unaffected.
    assert sorted(seen) == sorted(r.market_id_hex for r in refs)
    assert logger.warning.call_count == 1


async def test_fan_out_caps_in_flight_at_concurrency(mocker: MockerFixture) -> None:
    in_flight = 0
    peak = 0

    async def fake_execute(_activity: object, _ref: MarketTickRef, **_: object) -> None:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)  # hold the slot so overlap is observable
        in_flight -= 1

    mocker.patch(_EXEC_TARGET, new=fake_execute)
    mocker.patch(_LOGGER_TARGET)

    refs = [_ref(i) for i in range(10)]
    await _fan_out(refs, collect_market_snapshot, "collect_market_snapshot", 3)

    assert peak == 3  # never more than the batch size in flight at once
