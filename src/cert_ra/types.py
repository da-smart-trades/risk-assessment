# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003
from enum import StrEnum
from typing import NotRequired, TypedDict

from .utils import tz_now


class ChainType(StrEnum):
    ARBITRUM = "ARBITRUM"
    ETHEREUM = "ETHEREUM"
    SOLANA = "SOLANA"
    BASE = "BASE"
    INK = "INK"
    UNICHAIN = "UNICHAIN"
    POLYGON = "POLYGON"
    AVALANCHE_C = "AVALANCHE_C"
    OPTIMISM = "OPTIMISM"
    CANTON = "CANTON"

    @classmethod
    def get_chain_type(cls, val: str | None) -> ChainType | None:
        """Get the ChainType for a given string value."""
        if val is None:
            return None
        return cls.__members__.get(val.upper())


class TokenType(StrEnum):
    WETH = "WETH"
    USDE = "USDE"
    AAVE = "AAVE"
    UNI = "UNI"
    USDC = "USDC"
    USDT0 = "USDT0"
    AUSDC = "AUSDC"
    CUSDC = "CUSDC"
    CBBTC = "CBBTC"
    LINK = "LINK"
    STETH = "STETH"
    WSTETH = "WSTETH"

    @classmethod
    def get_token_type(cls, val: str | None) -> TokenType | None:
        """Get the TokenType for a given string value."""
        if val is None:
            return None
        return cls.__members__.get(val.upper())


class ProtocolType(StrEnum):
    AAVE_V3 = "AAVE_V3"
    MORPHO_V2 = "MORPHO_V2"
    COMPOUND_V3 = "COMPOUND_V3"
    DRIFT_V2 = "DRIFT_V2"

    @classmethod
    def get_protocol_type(cls, val: str | None) -> ProtocolType | None:
        """Get the ProtocolType for a given string value."""
        if val is None:
            return None
        return cls.__members__.get(val.upper())


class MarketSnapshotKind(StrEnum):
    """Discriminator for ``automated_market_snapshot`` rows.

    ``COLLECT`` rows hold ``metrics`` + ``evidence`` from the 5-minute
    Temporal collector run; ``SCORE`` rows additionally have ``score``
    populated from the hourly scorer run.
    """

    COLLECT = "COLLECT"
    SCORE = "SCORE"


class WeightingProfileScope(StrEnum):
    """Scope of a ``weighting_profile``: a single market or every market for a protocol."""

    MARKET = "MARKET"
    PROTOCOL = "PROTOCOL"


class WeightingProfileEntryCategory(StrEnum):
    """Category column on a ``weighting_profile_entry`` row.

    Singular names. The PD calculator maps the scorer JSON's
    ``score.anchors`` and ``score.controlModifiers`` keys onto ``ANCHOR``
    and ``CONTROL``; manual ASSURANCE metrics map onto ``ASSURANCE``.
    """

    ANCHOR = "ANCHOR"
    CONTROL = "CONTROL"
    ASSURANCE = "ASSURANCE"


class MetricCategory(StrEnum):
    """Closed set of manual-metric categories, scoped per entity type.

    The DB ``ck_manual_metric_entity_category`` CHECK constraint defines the
    valid (entity_type, category) pairs:

    * chain    → ``GOVERNANCE``
    * token    → ``ANCHORS`` / ``CONTROL`` / ``ASSURANCE`` / ``TOKEN_RISK``
                 / ``PROTOCOL_SCORE`` / ``TOKEN_SCORE``
    * protocol → ``ANCHORS`` / ``CONTROL`` / ``ASSURANCE`` / ``PROTOCOL_SCORE``
    * market   → ``ANCHORS`` / ``CONTROL`` / ``ASSURANCE`` / ``PROTOCOL_SCORE``

    ``TOKEN_RISK`` and ``PROTOCOL_SCORE`` are operator-only at the
    application layer: non-operator team editors cannot create manual
    metrics under these reserved categories.

    ``TOKEN_SCORE`` is system-written only.  The token-metrics seeder
    computes the final probability-of-default
    (``PD_base * M_control * M_assurance``) and stores it as a published
    ``TOKEN_SCORE / SUMMARY`` row (the display percentage in ``value``),
    mirroring the protocol ``PROTOCOL_SCORE / SUMMARY`` row.  It is the
    token's favoritable risk-score card: surfaced on the token page and
    read by the favorites resolver.

    ``MARKET_SCORE`` is the category label for the automatically-computed
    Probability-of-Default produced by the market scorer pipeline. It
    never appears on a ``ManualMetric`` row; the category exists so the
    favorites resolver and the frontend can refer to "a market score
    metric" symbolically (e.g., when grouping favorites or picking a
    display label).
    """

    GOVERNANCE = "GOVERNANCE"
    ANCHORS = "ANCHORS"
    CONTROL = "CONTROL"
    ASSURANCE = "ASSURANCE"
    TOKEN_RISK = "TOKEN_RISK"  # noqa: S105
    PROTOCOL_SCORE = "PROTOCOL_SCORE"
    TOKEN_SCORE = "TOKEN_SCORE"  # noqa: S105
    MARKET_SCORE = "MARKET_SCORE"

    @classmethod
    def get_metric_category(cls, val: str | None) -> MetricCategory | None:
        """Get the MetricCategory for a given string value."""
        if val is None:
            return None
        return cls.__members__.get(val.upper())


class MetricType(StrEnum):
    # Infrastructure & Performance
    TVL = "TVL"
    NUMBER_OF_NODES = "NUMBER_OF_NODES"
    NUMBER_OF_SOFTWARE_CLIENTS = "NUMBER_OF_SOFTWARE_CLIENTS"
    GAS_PRICE = "GAS_PRICE"
    TRANSACTIONS_PER_SECOND = "TRANSACTIONS_PER_SECOND"
    BLOCKS_PER_SECOND = "BLOCKS_PER_SECOND"

    # Security & Upgrades
    DELAY_ON_UPGRADE = "DELAY_ON_UPGRADE"
    EXIT_WINDOW = "EXIT_WINDOW"
    STATE_VALIDATION = "STATE_VALIDATION"
    UPGRADE_TRANSPARENCY = "UPGRADE_TRANSPARENCY"
    SLASHING_BEHAVIOR = "SLASHING_BEHAVIOR"
    LAST_RELEASE_DATE = "LAST_RELEASE_DATE"
    VITALIK_ROLLUP_MILESTONE = "VITALIK_ROLLUP_MILESTONE"

    # Decentralization
    TOTAL_AMOUNT_OF_STAKES = "TOTAL_AMOUNT_OF_STAKES"
    NAKAMOTO_LIVENESS_COEFFICIENT = "NAKAMOTO_LIVENESS_COEFFICIENT"
    NAKAMOTO_SAFETY_COEFFICIENT = "NAKAMOTO_SAFETY_COEFFICIENT"
    HHI = "HHI"
    RENYI_ENTROPY_ALPHA_0 = "RENYI_ENTROPY_ALPHA_0"
    RENYI_ENTROPY_ALPHA_1 = "RENYI_ENTROPY_ALPHA_1"
    RENYI_ENTROPY_ALPHA_2 = "RENYI_ENTROPY_ALPHA_2"
    RENYI_ENTROPY_ALPHA_INF = "RENYI_ENTROPY_ALPHA_INF"
    SHAPLEY_TOP_VALUE = "SHAPLEY_TOP_VALUE"
    SHAPLEY_SECOND_VALUE = "SHAPLEY_SECOND_VALUE"
    SHAPLEY_THIRD_VALUE = "SHAPLEY_THIRD_VALUE"
    # Canton Super-Validator governance decentralization. Canton SVs vote with
    # equal (one-SV-one-vote) BFT power, so the stake-weighted coefficients
    # above don't apply; the governance Nakamoto coefficient is a function of
    # the SV count and the >2/3 voting threshold instead.
    CANTON_GOV_NAKAMOTO_SAFETY = "CANTON_GOV_NAKAMOTO_SAFETY"
    CANTON_GOV_NAKAMOTO_LIVENESS = "CANTON_GOV_NAKAMOTO_LIVENESS"

    # USDC Metrics
    USDC_INFLOW = "USDC_INFLOW"
    USDC_OUTFLOW = "USDC_OUTFLOW"
    USDC_UNIQUE_ADDRESSES = "USDC_UNIQUE_ADDRESSES"
    USDC_TRANSACTION_COUNT = "USDC_TRANSACTION_COUNT"
    USDC_TOTAL_SUPPLY = "USDC_TOTAL_SUPPLY"

    # USDT0 Metrics
    USDT0_TOTAL_AMOUNT_TRANSFERS = "USDT0_TOTAL_AMOUNT_TRANSFERS"
    USDT0_INFLOW = "USDT0_INFLOW"
    USDT0_OUTFLOW = "USDT0_OUTFLOW"
    USDT0_UNIQUE_ADDRESSES = "USDT0_UNIQUE_ADDRESSES"
    USDT0_TRANSACTION_COUNT = "USDT0_TRANSACTION_COUNT"
    USDT0_TVL = "USDT0_TVL"

    # Finality
    ETH_FINALITY = "ETH_FINALITY"
    SOL_FINALITY = "SOL_FINALITY"
    ARB_FINALITY = "ARB_FINALITY"
    INK_FINALITY = "INK_FINALITY"
    POLYGON_FINALITY = "POLYGON_FINALITY"
    UNICHAIN_FINALITY = "UNICHAIN_FINALITY"
    BASE_FINALITY = "BASE_FINALITY"
    OPTIMISM_FINALITY = "OPTIMISM_FINALITY"
    CANTON_FINALITY = "CANTON_FINALITY"

    # Token Risk Metrics (Ethereum Mainnet)
    ETH_WETH_INFLOW = "ETH_WETH_INFLOW"
    ETH_WETH_OUTFLOW = "ETH_WETH_OUTFLOW"
    ETH_WETH_TOTAL_SUPPLY = "ETH_WETH_TOTAL_SUPPLY"
    ETH_USDE_TOTAL_SUPPLY = "ETH_USDE_TOTAL_SUPPLY"
    ETH_USDE_TRANSFER_COUNT = "ETH_USDE_TRANSFER_COUNT"
    ETH_USDE_UNIQUE_ADDRESSES = "ETH_USDE_UNIQUE_ADDRESSES"
    ETH_USDE_VOLUME = "ETH_USDE_VOLUME"
    ETH_AAVE_TOTAL_SUPPLY = "ETH_AAVE_TOTAL_SUPPLY"
    ETH_AAVE_TRANSFER_COUNT = "ETH_AAVE_TRANSFER_COUNT"
    ETH_AAVE_UNIQUE_ADDRESSES = "ETH_AAVE_UNIQUE_ADDRESSES"
    ETH_AAVE_VOLUME = "ETH_AAVE_VOLUME"
    ETH_UNI_TOTAL_SUPPLY = "ETH_UNI_TOTAL_SUPPLY"
    ETH_UNI_TRANSFER_COUNT = "ETH_UNI_TRANSFER_COUNT"
    ETH_UNI_UNIQUE_ADDRESSES = "ETH_UNI_UNIQUE_ADDRESSES"
    ETH_UNI_VOLUME = "ETH_UNI_VOLUME"
    ETH_AUSDC_TOTAL_SUPPLY = "ETH_AUSDC_TOTAL_SUPPLY"
    ETH_AUSDC_TRANSFER_COUNT = "ETH_AUSDC_TRANSFER_COUNT"
    ETH_AUSDC_UNIQUE_ADDRESSES = "ETH_AUSDC_UNIQUE_ADDRESSES"
    ETH_AUSDC_VOLUME = "ETH_AUSDC_VOLUME"
    ETH_CUSDC_TOTAL_SUPPLY = "ETH_CUSDC_TOTAL_SUPPLY"
    ETH_CUSDC_TRANSFER_COUNT = "ETH_CUSDC_TRANSFER_COUNT"
    ETH_CUSDC_UNIQUE_ADDRESSES = "ETH_CUSDC_UNIQUE_ADDRESSES"
    ETH_CUSDC_VOLUME = "ETH_CUSDC_VOLUME"
    ETH_LINK_TOTAL_SUPPLY = "ETH_LINK_TOTAL_SUPPLY"
    ETH_LINK_TRANSFER_COUNT = "ETH_LINK_TRANSFER_COUNT"
    ETH_LINK_UNIQUE_ADDRESSES = "ETH_LINK_UNIQUE_ADDRESSES"
    ETH_LINK_VOLUME = "ETH_LINK_VOLUME"
    ETH_STETH_TOTAL_SUPPLY = "ETH_STETH_TOTAL_SUPPLY"
    ETH_STETH_TRANSFER_COUNT = "ETH_STETH_TRANSFER_COUNT"
    ETH_STETH_UNIQUE_ADDRESSES = "ETH_STETH_UNIQUE_ADDRESSES"
    ETH_STETH_VOLUME = "ETH_STETH_VOLUME"
    ETH_WSTETH_TOTAL_SUPPLY = "ETH_WSTETH_TOTAL_SUPPLY"
    ETH_WSTETH_TRANSFER_COUNT = "ETH_WSTETH_TRANSFER_COUNT"
    ETH_WSTETH_UNIQUE_ADDRESSES = "ETH_WSTETH_UNIQUE_ADDRESSES"
    ETH_WSTETH_VOLUME = "ETH_WSTETH_VOLUME"
    ETH_CBBTC_TOTAL_SUPPLY = "ETH_CBBTC_TOTAL_SUPPLY"
    ETH_CBBTC_TRANSFER_COUNT = "ETH_CBBTC_TRANSFER_COUNT"
    ETH_CBBTC_UNIQUE_ADDRESSES = "ETH_CBBTC_UNIQUE_ADDRESSES"
    ETH_CBBTC_VOLUME = "ETH_CBBTC_VOLUME"

    # General Chain Activity
    UNIQUE_ADDRESSES = "UNIQUE_ADDRESSES"
    TRANSACTION_COUNT = "TRANSACTION_COUNT"

    # Governance
    VOTING_DELEGATION_LAYER_COEFFICIENT = "VOTING_DELEGATION_LAYER_COEFFICIENT"
    UPGRADE_AUTHORITY_COEFFICIENT = "UPGRADE_AUTHORITY_COEFFICIENT"
    EMERGENCY_POWERS_BYPASS_RISK = "EMERGENCY_POWERS_BYPASS_RISK"
    BASE_GOVERNANCE_EXECUTION = "BASE_GOVERNANCE_EXECUTION"
    SOLANA_GOVERNANCE_PROPOSALS = "SOLANA_GOVERNANCE_PROPOSALS"
    ETH_GOVERNANCE_PROPOSALS = "ETH_GOVERNANCE_PROPOSALS"
    ARB_GOVERNANCE_PROPOSALS = "ARB_GOVERNANCE_PROPOSALS"
    ARB_GOVERNANCE_EXECUTION = "ARB_GOVERNANCE_EXECUTION"
    ARB_GOVERNANCE_EMERGENCY = "ARB_GOVERNANCE_EMERGENCY"

    # Special cases
    DECENTRALIZATION_COMBINED = "DECENTRALIZATION_COMBINED"
    TIME_TO_FINALITY_SOFT = "TIME_TO_FINALITY_SOFT"


class Alert(TypedDict):
    chain: str
    time: int
    alert_type: str
    filter_key: str
    triggered: bool
    message: NotRequired[str]


class AlertRuleKind(StrEnum):
    """Discriminator for the polymorphic ``alert.rule_config`` JSONB column.

    Each kind has a corresponding msgspec validator registered in
    ``cert_ra.api.domain.alerts.rules``. ``COMPOSITE`` is reserved for a future
    multi-metric rule kind and currently has no validator.
    """

    THRESHOLD = "THRESHOLD"
    RATE_OF_CHANGE = "RATE_OF_CHANGE"
    STDDEV_DEVIATION = "STDDEV_DEVIATION"
    COMPOSITE = "COMPOSITE"


class AlertTargetKind(StrEnum):
    """Discriminator for the polymorphic ``alert.target_config`` JSONB column.

    Identifies what the alert is monitoring. ``METRIC`` is the original
    blockchain-metric path (``MetricType`` + chain + token). The ``MARKET_*``
    variants point at a specific ``(market_config_id, chain_id, market_id_hex)``
    triple — for ``MARKET_ANCHOR`` / ``MARKET_CONTROL`` the target also carries
    a ``sub_category`` selecting one entry from the scorer JSON.
    """

    METRIC = "METRIC"
    MARKET_PD = "MARKET_PD"
    MARKET_ANCHOR = "MARKET_ANCHOR"
    MARKET_CONTROL = "MARKET_CONTROL"


class AlertIntegrationKind(StrEnum):
    """Delivery channel for alert notifications.

    Each kind has a corresponding msgspec validator registered in
    ``cert_ra.api.domain.alerts.integrations`` and a dispatcher activity in
    ``cert_ra.alerts.activities``. ``SLACK`` and ``PAGERDUTY`` are reserved.
    """

    EMAIL = "EMAIL"
    WEBHOOK = "WEBHOOK"
    SLACK = "SLACK"
    PAGERDUTY = "PAGERDUTY"


class AlertSeverity(StrEnum):
    """Severity tier shown in the UI and surfaced in notification subject lines."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertHistoryStatus(StrEnum):
    """State of one evaluator-tick observation for an alert.

    ``OK`` is reserved for transitions; the evaluator only persists state-change
    rows (edge-trigger semantics), so a stable ``OK`` rule does not write a row
    on every tick. ``ERROR`` is emitted when the latest snapshot is too stale
    to evaluate against.
    """

    OK = "OK"
    TRIGGERED = "TRIGGERED"
    RECOVERED = "RECOVERED"
    ERROR = "ERROR"


class NotificationStatus(StrEnum):
    """Lifecycle state of a single notification delivery attempt."""

    PENDING = "PENDING"
    RETRYING = "RETRYING"
    SENT = "SENT"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


@dataclass(kw_only=True)
class Metric[T]:
    chain: ChainType
    value: T
    time: datetime = field(default_factory=tz_now)
    block_number: int | None = None
