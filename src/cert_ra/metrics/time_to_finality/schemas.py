# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# Chains with a configured soft-finality source. Each maps to a websocket
# subscription kind:
#   - ``eth_heads``:       ``eth_subscribe("newHeads")`` — Ethereum, Ink.
#   - ``eth_flashblocks``: ``eth_subscribe("newFlashblocks")`` — Base, Unichain.
#   - ``solana_slot``:     ``slotSubscribe`` — Solana.
SubscriptionKind = Literal["eth_heads", "eth_flashblocks", "solana_slot"]


CHAIN_SUBSCRIPTIONS: dict[str, SubscriptionKind] = {
    "ETHEREUM": "eth_heads",
    "INK": "eth_heads",
    "BASE": "eth_flashblocks",
    "UNICHAIN": "eth_flashblocks",
    "SOLANA": "solana_slot",
}


class TimeToFinalityResult(BaseModel):
    """Soft-finality snapshot: average seconds between successive new heads/slots."""

    chain: str
    soft_finality_seconds: float


class TimeToFinalityParams(BaseModel):
    """Single-chain time-to-finality workflow input."""

    chain: str
