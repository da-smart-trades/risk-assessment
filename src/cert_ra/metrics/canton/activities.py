# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Temporal activities for Canton Global Synchronizer metrics.

All data is sourced from the Splice Scan API via :class:`CantonScanClient`.
Parsing is defensive: the Scan responses are nested Daml-contract JSON whose
exact shapes can drift between Splice releases, so each helper reaches for the
fields it needs and tolerates absence rather than failing the whole snapshot.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from temporalio import activity

from cert_ra.db.models import DecentralizationCanton, FinalityCanton
from cert_ra.metrics._session import session_factory

from .scan_client import CantonScanClient
from .schemas import CantonDecentralizationResult, CantonFinalityResult

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: object) -> datetime | None:
    """Parse a Daml/ISO-8601 timestamp string into an aware datetime."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _seconds_since(value: object) -> float | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return (_now() - parsed).total_seconds()


def _coerce_int(value: object) -> int | None:
    """Coerce a Daml numeric (often a decimal string) to int."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _round_payload(round_obj: dict[str, Any]) -> dict[str, Any]:
    """Pull the ``contract.payload`` dict out of a mining-round entry."""
    contract = round_obj.get("contract")
    if isinstance(contract, dict):
        payload = contract.get("payload")
        if isinstance(payload, dict):
            return payload
    payload = round_obj.get("payload")
    return payload if isinstance(payload, dict) else {}


def _round_number(payload: dict[str, Any]) -> int | None:
    """Extract the round number; ``round`` may be a scalar or ``{"number": ...}``."""
    raw = payload.get("round")
    if isinstance(raw, dict):
        return _coerce_int(raw.get("number"))
    return _coerce_int(raw)


# ---------------------------------------------------------------------------
# Finality
# ---------------------------------------------------------------------------


def open_rounds_entries(rounds_resp: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize ``open_mining_rounds`` to a list of contract entries.

    The live Scan API returns a **map keyed by contract id**
    (``{cid: {contract: {payload: ...}}}``); older/documented samples used a
    plain list. Accept either so a shape change doesn't silently zero out the
    round metrics.
    """
    open_rounds = rounds_resp.get("open_mining_rounds")
    if isinstance(open_rounds, dict):
        return [v for v in open_rounds.values() if isinstance(v, dict)]
    if isinstance(open_rounds, list):
        return [v for v in open_rounds if isinstance(v, dict)]
    return []


def _latest_open_round(rounds_resp: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Return the (payload, count) for the *currently-open* mining round.

    ``open-and-issuing-mining-rounds`` returns several staggered rounds — the
    active one plus the next one or two whose ``opensAt`` is still in the
    future. We pick the round with the most recent past ``opensAt`` (the one
    actually open now) so ``round_advance_seconds`` is a positive
    time-since-opened stall signal, falling back to the highest-numbered round
    if none have opened yet.
    """
    entries = open_rounds_entries(rounds_resp)
    if not entries:
        return {}, 0
    now_ts = _now().timestamp()
    current: tuple[float, dict[str, Any]] | None = None
    fallback: tuple[int, dict[str, Any]] | None = None
    for entry in entries:
        payload = _round_payload(entry)
        number = _round_number(payload)
        if number is None:
            continue
        if fallback is None or number > fallback[0]:
            fallback = (number, payload)
        opened = _parse_iso(payload.get("opensAt"))
        if opened is not None:
            opened_ts = opened.timestamp()
            if opened_ts <= now_ts and (current is None or opened_ts > current[0]):
                current = (opened_ts, payload)
    if current is not None:
        return current[1], len(entries)
    return (fallback[1] if fallback else {}), len(entries)


async def _ledger_freshness_seconds(scan: CantonScanClient) -> float:
    """Seconds since the most recent ACS snapshot — a ledger-advance signal.

    Falls back to ``-1`` when the timestamp can't be read so the snapshot still
    persists (the negative sentinel is visible in the UI as "unknown").
    """
    from cert_ra.settings.canton import get_canton_settings

    # The endpoint needs a Z-suffixed UTC bound (a "+00:00" offset is mangled in
    # the query string) and the current synchronizer migration id.
    before = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        resp = await scan.get_acs_snapshot_timestamp(
            before=before, migration_id=get_canton_settings().migration_id
        )
    except Exception as exc:  # noqa: BLE001
        activity.logger.warning(f"canton_finality: snapshot-timestamp failed {exc}")
        return -1.0
    raw = resp.get("record_time") if isinstance(resp, dict) else resp
    if isinstance(resp, dict) and raw is None:
        raw = resp.get("timestamp")
    seconds = _seconds_since(raw)
    return seconds if seconds is not None else -1.0


@activity.defn
async def fetch_canton_finality() -> CantonFinalityResult:
    """Fetch a combined Canton finality snapshot from the Scan API."""
    async with CantonScanClient() as scan:
        dso = await scan.get_dso()
        rounds = await scan.get_open_and_issuing_mining_rounds()
        freshness = await _ledger_freshness_seconds(scan)

    voting_threshold = _coerce_int(dso.get("voting_threshold")) or 0
    sv_node_states = dso.get("sv_node_states")
    live_sv_count = len(sv_node_states) if isinstance(sv_node_states, list) else 0

    payload, open_round_count = _latest_open_round(rounds)
    if not payload:
        # Fall back to the DSO's latest mining round contract.
        latest = dso.get("latest_mining_round")
        if isinstance(latest, dict):
            payload = _round_payload(latest)

    round_number = _round_number(payload)
    if round_number is None:
        msg = "canton_finality: no mining round available from Scan"
        raise RuntimeError(msg)

    opens_at = payload.get("opensAt")
    target_closes_at = payload.get("targetClosesAt")
    advance = _seconds_since(opens_at)
    opened = _parse_iso(opens_at)
    closes = _parse_iso(target_closes_at)
    window = (closes - opened).total_seconds() if opened and closes else -1.0

    return CantonFinalityResult(
        latest_round_number=round_number,
        round_advance_seconds=advance if advance is not None else -1.0,
        round_window_seconds=window,
        open_round_count=open_round_count,
        ledger_freshness_seconds=freshness,
        live_sv_count=live_sv_count,
        voting_threshold=voting_threshold,
        sv_quorum_margin=live_sv_count - voting_threshold,
    )


@activity.defn
async def store_canton_finality(result: CantonFinalityResult) -> None:
    """Persist a Canton finality snapshot."""
    async with session_factory()() as session:
        session.add(
            FinalityCanton(
                latest_round_number=result.latest_round_number,
                round_advance_seconds=result.round_advance_seconds,
                round_window_seconds=result.round_window_seconds,
                open_round_count=result.open_round_count,
                ledger_freshness_seconds=result.ledger_freshness_seconds,
                live_sv_count=result.live_sv_count,
                voting_threshold=result.voting_threshold,
                sv_quorum_margin=result.sv_quorum_margin,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Decentralization (governance Nakamoto)
# ---------------------------------------------------------------------------


def _count_scans(scans_resp: dict[str, Any]) -> int:
    """Count distinct Super Validators across all synchronizer domains."""
    names: set[str] = set()
    domains = scans_resp.get("scans")
    if isinstance(domains, list):
        for domain in domains:
            if not isinstance(domain, dict):
                continue
            for entry in domain.get("scans", []) or []:
                if not isinstance(entry, dict):
                    continue
                # Prefer svName; fall back to publicUrl so a missing label
                # doesn't drop the SV from the count.
                key = entry.get("svName") or entry.get("publicUrl")
                if key:
                    names.add(str(key))
    return len(names)


def _count_sequencers(seq_resp: dict[str, Any]) -> int:
    """Count distinct sequencer ids across all synchronizer domains."""
    ids: set[str] = set()
    # The live API uses ``domainSequencers`` (camelCase); accept the documented
    # snake_case variants too.
    domains = (
        seq_resp.get("domainSequencers")
        or seq_resp.get("dso_sequencers")
        or seq_resp.get("domain_sequencers")
    )
    if isinstance(domains, list):
        for domain in domains:
            if not isinstance(domain, dict):
                continue
            for entry in domain.get("sequencers", []) or []:
                if isinstance(entry, dict) and entry.get("id"):
                    ids.add(str(entry["id"]))
    return len(ids)


async def _count_validators(scan: CantonScanClient) -> int:
    """Walk the paginated validator-licenses endpoint and count entries."""
    from cert_ra.settings.canton import get_canton_settings

    settings = get_canton_settings()
    total = 0
    after: str | None = None
    for _ in range(settings.validator_license_max_pages):
        resp = await scan.get_validator_licenses(
            page_size=settings.validator_license_page_size, after=after
        )
        licenses = resp.get("validator_licenses")
        if isinstance(licenses, list):
            total += len(licenses)
        after = resp.get("next_page_token")
        if not after:
            break
    else:
        activity.logger.warning(
            "canton_decentralization: validator-license pagination hit page cap "
            f"({settings.validator_license_max_pages}); count is a floor"
        )
    return total


@activity.defn
async def fetch_canton_decentralization() -> CantonDecentralizationResult:
    """Fetch the Canton Super-Validator governance-decentralization snapshot."""
    async with CantonScanClient() as scan:
        dso = await scan.get_dso()
        scans = await scan.get_scans()
        sequencers = await scan.get_dso_sequencers()
        validator_count = await _count_validators(scan)

    voting_threshold = _coerce_int(dso.get("voting_threshold")) or 0

    sv_count = _count_scans(scans)
    if sv_count == 0:
        # Fall back to the DSO's SV node-state list.
        node_states = dso.get("sv_node_states")
        sv_count = len(node_states) if isinstance(node_states, list) else 0
    if sv_count == 0:
        msg = "canton_decentralization: could not determine SV count from Scan"
        raise RuntimeError(msg)

    # Equal-vote BFT: blocking a >2/3 vote needs floor(N/3)+1 SVs; stalling
    # governance needs enough SVs offline to drop below the voting threshold.
    gov_nakamoto_safety = math.floor(sv_count / 3) + 1
    gov_nakamoto_liveness = max(1, sv_count - voting_threshold + 1)

    return CantonDecentralizationResult(
        sv_count=sv_count,
        validator_count=validator_count,
        voting_threshold=voting_threshold,
        gov_nakamoto_safety=gov_nakamoto_safety,
        gov_nakamoto_liveness=gov_nakamoto_liveness,
        distinct_sequencer_count=_count_sequencers(sequencers),
    )


@activity.defn
async def store_canton_decentralization(
    result: CantonDecentralizationResult,
) -> None:
    """Persist a Canton decentralization snapshot."""
    async with session_factory()() as session:
        session.add(
            DecentralizationCanton(
                sv_count=result.sv_count,
                validator_count=result.validator_count,
                voting_threshold=result.voting_threshold,
                gov_nakamoto_safety=result.gov_nakamoto_safety,
                gov_nakamoto_liveness=result.gov_nakamoto_liveness,
                distinct_sequencer_count=result.distinct_sequencer_count,
            )
        )
        await session.commit()
