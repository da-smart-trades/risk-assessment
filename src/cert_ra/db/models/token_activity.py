# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from decimal import Decimal  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import Index, Numeric
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from cert_ra.types import ChainType, MetricType, TokenType


class TokenActivity(UUIDAuditBase):
    """Per-token activity snapshot for a chain (inflow, outflow, supply, ...).

    A single row holds one ``(chain, token, metric_type)`` measurement; the
    ``metric_type`` discriminator selects between USDC/USDT0/WETH/USDe/AAVE/UNI
    inflow, outflow, transfer count, unique addresses, total supply and volume
    metrics.
    """

    __tablename__ = "token_activity"
    __table_args__ = (
        Index(
            "ix_token_activity_chain_token_metric_created_at",
            "chain",
            "token",
            "metric_type",
            "created_at",
        ),
    )

    chain: Mapped[ChainType] = mapped_column(
        ENUM(ChainType), nullable=False, index=True
    )
    token: Mapped[TokenType] = mapped_column(
        ENUM(TokenType), nullable=False, index=True
    )
    metric_type: Mapped[MetricType] = mapped_column(
        ENUM(MetricType), nullable=False, index=True
    )
    value: Mapped[Decimal] = mapped_column(
        Numeric(precision=30, scale=8), nullable=False
    )
