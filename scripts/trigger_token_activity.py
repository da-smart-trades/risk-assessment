#!/usr/bin/env python3
"""Manually trigger Temporal ``token-activity-*`` schedules.

The schedules normally fire every hour (see ``cert_ra.metrics.worker``).
This script forces an immediate run for each ``(chain, token)`` pair so a
developer can see fresh ``token_activity`` rows without waiting for the
next scheduled tick — useful after restarting the worker with new fetch
or projection logic.

Usage:
    # Trigger all 18 supported pairs.
    uv run python scripts/trigger_token_activity.py

    # Trigger only pairs matching a chain or token (case-insensitive).
    uv run python scripts/trigger_token_activity.py --chain ETHEREUM
    uv run python scripts/trigger_token_activity.py --token USDC
    uv run python scripts/trigger_token_activity.py --chain ETHEREUM --token USDC

Each trigger fans out to the same workflow / activity pair that the
hourly schedule invokes, so Dune rate limits and the worker's retry
policy apply. Firing all 18 in parallel will typically hit Dune 429s;
the worker's ``_FETCH_RETRY`` backoff will drain them over a few minutes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from cert_ra.metrics.tokens.schemas import SUPPORTED_PAIRS
from cert_ra.settings.temporal import get_temporal_settings


def _schedule_id(chain: str, token: str) -> str:
    """Mirror the id format used by ``cert_ra.metrics.worker._SCHEDULES``."""
    return f"token-activity-{chain.lower()}-{token.lower()}"


def _select_pairs(
    chain_filter: str | None, token_filter: str | None
) -> list[tuple[str, str]]:
    chain_u = chain_filter.upper() if chain_filter else None
    token_u = token_filter.upper() if token_filter else None
    return [
        (c, t)
        for c, t in SUPPORTED_PAIRS
        if (chain_u is None or c == chain_u) and (token_u is None or t == token_u)
    ]


async def _trigger(client: Client, chain: str, token: str) -> tuple[str, str | None]:
    sid = _schedule_id(chain, token)
    try:
        await client.get_schedule_handle(sid).trigger()
    except Exception as exc:  # noqa: BLE001 — surface any client/RPC failure verbatim
        return sid, repr(exc)
    return sid, None


async def _main(chain_filter: str | None, token_filter: str | None) -> int:
    pairs = _select_pairs(chain_filter, token_filter)
    if not pairs:
        print(  # noqa: T201
            f"No supported pairs match chain={chain_filter!r} token={token_filter!r}",
            file=sys.stderr,
        )
        return 2

    settings = get_temporal_settings()
    client = await Client.connect(
        settings.host,
        namespace=settings.namespace,
        rpc_metadata={"temporal-namespace": settings.namespace}
        if settings.api_key
        else {},
        api_key=settings.api_key or None,
        data_converter=pydantic_data_converter,
    )

    results = await asyncio.gather(*(_trigger(client, c, t) for c, t in pairs))
    ok = [sid for sid, err in results if err is None]
    failed = [(sid, err) for sid, err in results if err is not None]

    print(f"triggered ok: {len(ok)}/{len(results)}")  # noqa: T201
    for sid in ok:
        print(f"  + {sid}")  # noqa: T201
    if failed:
        print("FAILURES:", file=sys.stderr)  # noqa: T201
        for sid, err in failed:
            print(f"  - {sid}: {err}", file=sys.stderr)  # noqa: T201
        return 1
    return 0


def main() -> None:
    """Parse CLI args and trigger the selected token-activity schedules."""
    parser = argparse.ArgumentParser(
        description="Trigger Temporal token-activity-* schedules immediately."
    )
    parser.add_argument(
        "--chain",
        help="Restrict to one chain (e.g. ETHEREUM, SOLANA). Case-insensitive.",
    )
    parser.add_argument(
        "--token", help="Restrict to one token (e.g. USDC, WETH). Case-insensitive."
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.chain, args.token)))


if __name__ == "__main__":
    main()
