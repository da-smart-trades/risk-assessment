# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Canton throughput fetch, sourced from the Splice Scan API.

Canton has no blocks or gas, so the shared throughput shape is mapped onto its
closest native equivalents:

* ``transactions_per_second`` ← updates/sec from the bulk ``/v2/updates`` stream
* ``blocks_per_second``       ← rounds/sec (the ~10-minute economic round is
  Canton's native time unit)
* ``gas_price``               ← ``amuletPrice`` (USD per Canton Coin), the
  network's headline economic-state scalar

This lives in the throughput package (alongside ``dune.py`` / ``evm_rpc.py``)
so it plugs into the existing :class:`ThroughputWorkflow`; it reuses the shared
``CantonScanClient`` and parsing helpers from ``cert_ra.metrics.canton``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import activity

from cert_ra.metrics.canton.activities import (
    _coerce_float,
    _now,
    _parse_iso,
    _round_number,
    _round_payload,
    open_rounds_entries,
)
from cert_ra.metrics.canton.scan_client import CantonScanClient
from cert_ra.settings.canton import get_canton_settings

from .schemas import ThroughputResult

# Nominal seconds-per-round fallback when only one open round is visible and a
# measured cadence can't be derived (rounds open every ~10 minutes by default).
_NOMINAL_ROUND_SECONDS = 600.0

# Minimum open rounds needed to measure cadence from consecutive ``opensAt`` gaps.
_MIN_ROUNDS_FOR_CADENCE = 2


def _rounds_per_second(rounds_resp: dict[str, Any]) -> float:
    """Estimate rounds/sec from the spacing of consecutive open rounds' ``opensAt``."""
    opens: list[tuple[int, float]] = []
    for entry in open_rounds_entries(rounds_resp):
        payload = _round_payload(entry)
        number = _round_number(payload)
        opened = _parse_iso(payload.get("opensAt"))
        if number is not None and opened is not None:
            opens.append((number, opened.timestamp()))
    if len(opens) < _MIN_ROUNDS_FOR_CADENCE:
        return 1.0 / _NOMINAL_ROUND_SECONDS
    opens.sort()
    deltas = [
        opens[i + 1][1] - opens[i][1]
        for i in range(len(opens) - 1)
        if opens[i + 1][1] > opens[i][1]
    ]
    if not deltas:
        return 1.0 / _NOMINAL_ROUND_SECONDS
    seconds_per_round = sum(deltas) / len(deltas)
    return (
        1.0 / seconds_per_round
        if seconds_per_round > 0
        else 1.0 / _NOMINAL_ROUND_SECONDS
    )


def _amulet_price(rounds_resp: dict[str, Any]) -> float:
    """Read ``amuletPrice`` (USD/CC) from the highest-numbered open round."""
    best_price = 0.0
    best_number = -1
    for entry in open_rounds_entries(rounds_resp):
        payload = _round_payload(entry)
        number = _round_number(payload)
        price = _coerce_float(payload.get("amuletPrice"))
        if number is not None and price is not None and number > best_number:
            best_number = number
            best_price = price
    return best_price


async def _updates_per_second(scan: CantonScanClient) -> float:
    """Count updates in the recent window via ``/v2/updates`` → updates/sec."""
    settings = get_canton_settings()
    window = settings.updates_window_seconds
    after = (_now() - timedelta(seconds=window)).isoformat()
    try:
        resp = await scan.get_updates(
            after_record_time=after,
            after_migration_id=settings.migration_id,
            page_size=settings.updates_page_size,
        )
    except Exception as exc:  # noqa: BLE001
        activity.logger.warning(f"canton_throughput: /v2/updates failed {exc}")
        return -1.0

    if isinstance(resp, list):
        updates = resp
    elif isinstance(resp, dict):
        updates = resp.get("transactions") or resp.get("updates") or []
    else:
        updates = []

    count = len(updates) if isinstance(updates, list) else 0
    if count >= settings.updates_page_size:
        activity.logger.warning(
            "canton_throughput: update window hit page cap "
            f"({settings.updates_page_size}); updates/sec is a floor"
        )
    return count / window if window > 0 else 0.0


async def fetch_canton_throughput() -> ThroughputResult:
    """Fetch Canton throughput (updates/sec, rounds/sec, amulet price)."""
    async with CantonScanClient() as scan:
        rounds = await scan.get_open_and_issuing_mining_rounds()
        updates_per_second = await _updates_per_second(scan)

    return ThroughputResult(
        chain="CANTON",
        gas_price=_amulet_price(rounds),
        transactions_per_second=updates_per_second,
        blocks_per_second=_rounds_per_second(rounds),
    )
