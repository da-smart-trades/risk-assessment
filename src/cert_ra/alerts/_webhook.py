# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""HMAC-signed webhook dispatcher.

Built as a thin standalone helper so the dispatcher activity can call it
directly without going through the Litestar app. Same shape Stripe and GitHub
use: ``HMAC-SHA256(secret, body)`` digest in a custom header. The receiver
stores the same secret and verifies the header before accepting the event.

Key constraints:

- The body must be the **exact bytes** signed; ``json.dumps`` with
  ``sort_keys=True, separators=(",", ":")`` keeps the hash stable across
  Python versions.
- A short connect/read timeout (10 s total) prevents a slow webhook from
  blocking the dispatcher worker.
- Stable ``event_id`` and ``alert_history_id`` fields in the payload let
  receivers de-duplicate, since Temporal retries can deliver the same
  notification twice during partial failures.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

__all__ = ("SIGNATURE_HEADER", "compute_signature", "deliver_webhook")


SIGNATURE_HEADER = "X-CRA-Signature"
"""Header carrying the HMAC-SHA256 signature of the request body."""


def _canonical_body(payload: dict[str, Any]) -> bytes:
    """Serialise ``payload`` deterministically so the signature is stable.

    Sorts keys, eliminates whitespace; UTF-8 encoded bytes are what gets signed
    and what gets sent on the wire.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_signature(body: bytes, secret: str) -> str:
    """Return the hex-encoded HMAC-SHA256 of ``body`` keyed by ``secret``."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def deliver_webhook(
    url: str,
    secret: str,
    payload: dict[str, Any],
    *,
    extra_headers: dict[str, str] | None = None,
    timeout_seconds: float = 10.0,
) -> tuple[bool, str | None]:
    """POST ``payload`` to ``url`` with a signed body.

    Args:
        url: Destination URL.
        secret: Cleartext shared secret (decrypted by the dispatcher just
            before this call).
        payload: JSON-serialisable mapping; sent as the request body.
        extra_headers: Optional caller-supplied headers (e.g. tenant tags).
            ``Content-Type`` and the signature header are always set by this
            function and override any caller value.
        timeout_seconds: Total timeout for connect + read. Keep small —
            webhook receivers should be quick.

    Returns:
        ``(True, None)`` on a 2xx response. ``(False, reason)`` on any other
        status, network error, or timeout.
    """
    body = _canonical_body(payload)
    signature = compute_signature(body, secret)
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: signature,
    }
    if extra_headers:
        for k, v in extra_headers.items():
            if k.lower() in {"content-type", SIGNATURE_HEADER.lower()}:
                continue
            headers[k] = v
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        return False, f"HTTP error: {exc.__class__.__name__}: {exc}"
    if response.is_success:
        return True, None
    return (
        False,
        f"HTTP {response.status_code}: {response.text[:200]}",
    )
