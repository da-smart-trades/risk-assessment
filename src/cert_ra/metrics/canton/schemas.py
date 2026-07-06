# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from pydantic import BaseModel


class CantonFinalityResult(BaseModel):
    """Combined Canton finality snapshot.

    Canton finality is deterministic (a transaction is final the moment the
    BFT-ordered two-phase commit completes), so there is no Ethereum-style
    safe/finalized height gradient to measure. Instead this captures the two
    health signals that *can* stall:

    * **Cadence / freshness** — is the network still advancing? Rounds open on
      a ~10-minute cycle and the ledger should keep producing updates.
    * **Consensus safety** — how much headroom does the SV BFT quorum have
      above its >2/3 voting threshold?
    """

    # Cadence / freshness
    latest_round_number: int
    round_advance_seconds: float
    """Wall-clock seconds since the latest open round opened (``opensAt``)."""
    round_window_seconds: float
    """Nominal open window of the latest round (``targetClosesAt - opensAt``)."""
    open_round_count: int
    ledger_freshness_seconds: float
    """Seconds since the most recent ACS snapshot / observed update."""

    # Consensus safety
    live_sv_count: int
    voting_threshold: int
    sv_quorum_margin: int
    """``live_sv_count - voting_threshold`` — SVs that could drop before the
    BFT quorum is lost (negative means quorum already cannot be met)."""


class CantonDecentralizationResult(BaseModel):
    """Governance-decentralization snapshot for the Canton Super-Validator set.

    Super Validators vote with equal (one-SV-one-vote) BFT power, so the
    stake-weighted measures used for PoS chains (HHI, Shapley, Rényi entropy)
    are degenerate here. The governance Nakamoto coefficient — derived from the
    SV count ``N`` and the >2/3 voting threshold — is the meaningful number.
    """

    sv_count: int
    validator_count: int
    voting_threshold: int
    gov_nakamoto_safety: int
    """Min SVs needed to *block* a >2/3 governance vote: ``floor(N/3) + 1``."""
    gov_nakamoto_liveness: int
    """Min SVs whose outage stalls governance: ``N - voting_threshold + 1``."""
    distinct_sequencer_count: int
