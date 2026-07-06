# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import asyncio
import re

import httpx
from pydantic import BaseModel
from temporalio import activity

from cert_ra.db.models import GovernanceEvent
from cert_ra.metrics._session import session_factory
from cert_ra.settings.rpc import get_rpc_settings
from cert_ra.types import ChainType

from .rpc import count_evm_events
from .schemas import SUPPORTED_EVENTS, GovernanceParams, GovernanceResult

# Upstream URLs / contract addresses (mirror the old-setup constants).
_ETH_EIPS_REPO = "ethereum/EIPs"
_SOL_SIMD_REPO = "solana-foundation/solana-improvement-documents"

# Next-mainnet-hardfork meta-EIP. Update this constant when the next hardfork
# meta-EIP is published (typically every 6-12 months). The fetcher parses the
# meta-EIP body for distinct ``EIP-<number>`` references, treating each as a
# confirmed inclusion.
_ETH_NEXT_META_EIP = 7607  # TODO: bump to next hardfork's meta-EIP when scheduled

# Raw markdown for a specific EIP, served by GitHub's CDN (no rate limit
# against the API quota). Trailing ``{eip}`` is substituted at call time.
_ETH_EIP_RAW_URL = (
    "https://raw.githubusercontent.com/ethereum/EIPs/master/EIPS/eip-{eip}.md"
)

# Recursive tree listing for the EIPs repo, used to enumerate every
# ``EIPS/eip-*.md`` path in a single API call.
_ETH_EIPS_TREE_URL = (
    f"https://api.github.com/repos/{_ETH_EIPS_REPO}/git/trees/master?recursive=1"
)

# Max concurrent raw-content fetches when scanning every EIP file for
# ``status: Last Call``. ~700 files / 10 concurrent ~= 70 sequential rounds.
_ETH_LAST_CALL_CONCURRENCY = 10

_ARB_FORUM_URL = "https://forum.arbitrum.foundation/c/proposals/7.json"

_GITHUB_API = "https://api.github.com"
_GITHUB_PER_PAGE = 100

# Matches ``EIP-1234`` or ``eip-1234`` (case-insensitive), capturing the number.
# Anchored on word boundaries so it doesn't match e.g. ``eip-1234567890`` runs.
_EIP_REF_RE = re.compile(r"\bEIP-(\d+)\b", re.IGNORECASE)

# Arbitrum Timelock contract + CallScheduled / CallExecuted event topics.
_ARB_TIMELOCK = "0x34d45e99f7D8c45ed05B5cA72D54bbD1fb3F98f0"
_ARB_TIMELOCK_TOPICS: list[list[str]] = [
    [
        # CallScheduled(bytes32 indexed id, uint256 indexed index, address target,
        #               uint256 value, bytes data, bytes32 predecessor, uint256 delay)
        "0x4cf4410cc57040e44862ef0f45f3dd5a5e02db8eb8add648d4b0e236f1d07dca",
        # CallExecuted(bytes32 indexed id, uint256 indexed index, address target,
        #              uint256 value, bytes data)
        "0xc2617efa69bab66782fa219543714338489c4e9e178271560a91b82c3f612b58",
    ]
]
# Arbitrum Security Council UpgradeExecutor — all events count as emergency.
_ARB_UPGRADE_EXECUTOR = "0xCF57572261c7c2BCF21ffD220ea7d1a27D40A827"
# Base UpgradeExecutor — all events count as execution authority signal.
_BASE_UPGRADE_EXECUTOR = "0x14536667Cd30e52C0b458BaACcB9faDA7046E056"

# Approximate slot times used to translate the 6h workflow interval into a
# block lookback per chain.
_ARB_LOOKBACK_BLOCKS = 86_400  # ~6h at 0.25s/block
_BASE_LOOKBACK_BLOCKS = 10_800  # ~6h at 2s/block


# ---------------------------------------------------------------------------
# Tiny pydantic shapes for upstream response parsing
# ---------------------------------------------------------------------------


class _DiscourseTopic(BaseModel):
    id: int


# ---------------------------------------------------------------------------
# Per-feed fetchers
# ---------------------------------------------------------------------------


async def _count_open_prs(repo: str) -> int:
    """Return the number of open PRs in ``repo`` (capped at one page)."""
    url = f"{_GITHUB_API}/repos/{repo}/pulls"
    params = {
        "state": "open",
        "sort": "created",
        "direction": "desc",
        "per_page": str(_GITHUB_PER_PAGE),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        items = response.json()
    if not isinstance(items, list):
        msg = f"governance: GitHub returned non-list payload for {repo}"
        raise TypeError(msg)
    return sum(1 for raw in items if isinstance(raw, dict) and "number" in raw)


async def _count_forum_topics(url: str) -> int:
    """Return the number of topics returned by a Discourse JSON feed."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        msg = f"governance: Discourse forum at {url} returned non-object payload"
        raise TypeError(msg)
    raw_topics = payload.get("topic_list", {}).get("topics", [])
    if not isinstance(raw_topics, list):
        return 0
    valid = [
        _DiscourseTopic.model_validate(t) for t in raw_topics if isinstance(t, dict)
    ]
    return len(valid)


async def _fetch_eth_confirmed_eips() -> int:
    """Count distinct ``EIP-<n>`` references in the next-hardfork meta-EIP.

    The meta-EIP is a markdown document in ``ethereum/EIPs`` whose body lists
    the EIPs confirmed for inclusion in the upgrade. We treat every distinct
    ``EIP-<number>`` reference in the body (anywhere, not just within a
    specific section) as a confirmed inclusion. This is approximate: it can
    include EIPs that are merely cross-referenced in prose. The meta-EIP's
    own number is excluded.
    """
    url = _ETH_EIP_RAW_URL.format(eip=_ETH_NEXT_META_EIP)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        body = response.text
    distinct = {
        int(match.group(1))
        for match in _EIP_REF_RE.finditer(body)
        if int(match.group(1)) != _ETH_NEXT_META_EIP
    }
    return len(distinct)


async def _fetch_eth_last_call_eips() -> int:
    """Count EIPs in ``ethereum/EIPs`` whose frontmatter status is ``Last Call``.

    Lists ``EIPS/eip-*.md`` paths via the git-tree API (one call), then fetches
    each file's raw markdown from ``raw.githubusercontent.com`` (no API quota).
    Concurrency is bounded by ``_ETH_LAST_CALL_CONCURRENCY``. Files we can't
    fetch are skipped rather than failing the whole count — Temporal retries
    if the tree listing itself fails.
    """
    eip_numbers = await _list_eip_numbers()
    semaphore = asyncio.Semaphore(_ETH_LAST_CALL_CONCURRENCY)

    async with httpx.AsyncClient(timeout=30.0) as client:

        async def fetch_status(eip: int) -> str | None:
            async with semaphore:
                try:
                    raw = await client.get(_ETH_EIP_RAW_URL.format(eip=eip))
                    raw.raise_for_status()
                except httpx.HTTPError:
                    return None
                return _parse_frontmatter_status(raw.text)

        statuses = await asyncio.gather(*(fetch_status(n) for n in eip_numbers))

    return sum(1 for s in statuses if s is not None and s.lower() == "last call")


async def _list_eip_numbers() -> list[int]:
    """Return every EIP number present as ``EIPS/eip-<n>.md`` on ``master``."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(_ETH_EIPS_TREE_URL)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        msg = "governance: GitHub tree listing for ethereum/EIPs returned non-object"
        raise TypeError(msg)
    tree = payload.get("tree", [])
    if not isinstance(tree, list):
        return []
    pattern = re.compile(r"^EIPS/eip-(\d+)\.md$")
    numbers: list[int] = []
    for entry in tree:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str):
            continue
        match = pattern.match(path)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def _parse_frontmatter_status(markdown: str) -> str | None:
    """Extract the ``status:`` value from an EIP markdown file's YAML frontmatter.

    EIP frontmatter is a ``---``-delimited YAML block at the top of every EIP.
    We don't pull in a YAML parser because the frontmatter format is rigid:
    ``key: value`` lines with no nesting. Returns ``None`` if no frontmatter
    or no ``status`` key is present.
    """
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return None
        if stripped.lower().startswith("status:"):
            return stripped.split(":", 1)[1].strip()
    return None


async def _fetch_sol_proposals() -> int:
    return await _count_open_prs(_SOL_SIMD_REPO)


async def _fetch_arb_proposals() -> int:
    return await _count_forum_topics(_ARB_FORUM_URL)


async def _fetch_arb_execution() -> int:
    """Count Arbitrum Timelock CallScheduled / CallExecuted events in the last ~6h."""
    rpc = get_rpc_settings()
    return await count_evm_events(
        urls=rpc.arbitrum_urls,
        address=_ARB_TIMELOCK,
        topics=_ARB_TIMELOCK_TOPICS,
        lookback_blocks=_ARB_LOOKBACK_BLOCKS,
        label="arb_timelock",
    )


async def _fetch_arb_emergency() -> int:
    """Count all events on the Arbitrum Security Council UpgradeExecutor in the last ~6h."""
    rpc = get_rpc_settings()
    return await count_evm_events(
        urls=rpc.arbitrum_urls,
        address=_ARB_UPGRADE_EXECUTOR,
        topics=None,
        lookback_blocks=_ARB_LOOKBACK_BLOCKS,
        label="arb_upgrade_executor",
    )


async def _fetch_base_execution() -> int:
    """Count all events on the Base UpgradeExecutor in the last ~6h."""
    rpc = get_rpc_settings()
    return await count_evm_events(
        urls=rpc.base_urls,
        address=_BASE_UPGRADE_EXECUTOR,
        topics=None,
        lookback_blocks=_BASE_LOOKBACK_BLOCKS,
        label="base_upgrade_executor",
    )


_FETCHERS = {
    ("ETHEREUM", "confirmed_eips"): _fetch_eth_confirmed_eips,
    ("ETHEREUM", "last_call_eips"): _fetch_eth_last_call_eips,
    ("ARBITRUM", "proposals"): _fetch_arb_proposals,
    ("ARBITRUM", "execution"): _fetch_arb_execution,
    ("ARBITRUM", "emergency"): _fetch_arb_emergency,
    ("BASE", "execution"): _fetch_base_execution,
    ("SOLANA", "proposals"): _fetch_sol_proposals,
}


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


@activity.defn
async def fetch_governance(params: GovernanceParams) -> GovernanceResult:
    """Fetch the latest governance event count for ``(chain, event_type)``."""
    chain_upper = params.chain.upper()
    event_type = params.event_type.lower()
    key = (chain_upper, event_type)
    if key not in SUPPORTED_EVENTS:
        msg = f"governance: pair {key!r} not supported"
        raise ValueError(msg)

    fetcher = _FETCHERS[key]
    count = await fetcher()
    return GovernanceResult(
        chain=chain_upper,
        event_type=event_type,
        count=count,
    )


@activity.defn
async def store_governance(result: GovernanceResult) -> None:
    """Persist a governance event count to the database."""
    async with session_factory()() as session:
        session.add(
            GovernanceEvent(
                chain=ChainType(result.chain),
                event_type=result.event_type,
                count=result.count,
            )
        )
        await session.commit()
