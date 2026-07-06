# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Alerts subsystem — Temporal evaluator + dispatcher worker.

This package is a *standalone* Temporal worker, sibling to ``cert_ra.metrics``.
It runs on the ``"alerts"`` task queue and is started independently from the
metrics worker so that an alerting-side stall (stuck webhook, slow SMTP) cannot
take down metric ingestion.

Entrypoints:

- ``cert_ra.alerts.worker:main`` — the ``certora-risk-alerts-worker`` script.
- ``python -m cert_ra.alerts.worker`` — equivalent module form (``make alerts-worker``).

The worker:

1. Connects to the same Temporal namespace as the metrics worker.
2. Registers two schedules — ``alerts-evaluator`` (30 s) and
   ``alerts-notification-dispatch`` (15 s).
3. Polls the ``"alerts"`` task queue and runs the registered workflows.

The whole worker is gated behind ``cert_ra_temporal_alerts_enabled``; if the
flag is false the worker exits with a log message rather than connecting.
"""
