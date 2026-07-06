# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class RPCSettings(BaseSettings):
    """RPC endpoint URLs for blockchain data fetching.

    Each multi-provider chain exposes two private RPC slots (e.g. Alchemy /
    Infura / QuickNode) plus a list of public fallbacks. A computed
    ``<chain>_urls`` property returns them in priority order: private 1,
    private 2, then public URLs.

    Ink and Unichain remain single-URL because ``optimism_syncStatus`` is
    provider-specific (QuickNode / PrivateRPC respectively) and a generic
    fallback would fail the call.

    Environment variable examples::

        CERT_RA_RPC_ETHEREUM_PRIVATE_RPC_1=https://eth-mainnet.alchemy.example
        CERT_RA_RPC_ETHEREUM_PUBLIC_RPCS='["https://eth-rpc.example.com"]'
        CERT_RA_RPC_INK_URL=https://ink-quicknode.example.com
    """

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_rpc_", case_sensitive=False, extra="ignore"
    )

    ethereum_private_rpc_1: str | None = None
    """Private Ethereum RPC URL (e.g. Alchemy, Infura) for fetching execution"""
    ethereum_private_rpc_2: str | None = None
    """Secondary private Ethereum RPC URL for redundancy."""
    ethereum_public_rpcs: list[str] = []
    """Public Ethereum RPC URLs (tried in order)."""

    arbitrum_private_rpc_1: str | None = None
    """Private Arbitrum RPC URL (e.g. Alchemy, Infura)."""
    arbitrum_private_rpc_2: str | None = None
    """Secondary private Arbitrum RPC URL for redundancy."""
    arbitrum_public_rpcs: list[str] = [
        "https://arb1.arbitrum.io/rpc",
        "https://arbitrum-one-rpc.publicnode.com",
    ]
    """Public Arbitrum RPC URLs (tried in order)."""

    base_private_rpc_1: str | None = None
    """Private Base RPC URL (e.g. Alchemy, QuickNode)."""
    base_private_rpc_2: str | None = None
    """Secondary private Base RPC URL for redundancy."""
    base_public_rpcs: list[str] = [
        "https://base-rpc.publicnode.com",
        "https://1rpc.io/base",
    ]
    """Public Base RPC URLs (tried in order)."""

    polygon_private_rpc_1: str | None = None
    """Private Polygon RPC URL (e.g. Alchemy, Infura)."""
    polygon_private_rpc_2: str | None = None
    """Secondary private Polygon RPC URL for redundancy."""
    polygon_public_rpcs: list[str] = [
        "https://rpc-mainnet.matic.quiknode.pro",
    ]
    """Public Polygon RPC URLs (tried in order)."""

    optimism_private_rpc_1: str | None = None
    """Private Optimism RPC URL (e.g. Alchemy, QuickNode)."""
    optimism_private_rpc_2: str | None = None
    """Secondary private Optimism RPC URL for redundancy."""
    optimism_public_rpcs: list[str] = [
        "https://mainnet.optimism.io",
        "https://optimism-rpc.publicnode.com",
    ]
    """Public Optimism RPC URLs (tried in order)."""

    solana_private_rpc_1: str | None = None
    """Private Solana RPC URL (e.g. Helius, QuickNode)."""
    solana_private_rpc_2: str | None = None
    """Secondary private Solana RPC URL for redundancy."""
    solana_public_rpcs: list[str] = [
        "https://api.mainnet-beta.solana.com",
        "https://solana-api.projectserum.com",
        "https://rpc.ankr.com/solana",
    ]
    """Public Solana RPC URLs (tried in order)."""

    # NOTE: Avalanche RPC settings are split into P-Chain (validator/staking
    # data) and C-Chain (EVM execution). Decentralization metrics hit P-Chain;
    # throughput hits C-Chain. Env vars renamed: ``AVALANCHE_*`` ->
    # ``AVALANCHE_P_*``.
    avalanche_p_private_rpc_1: str | None = None
    """Private Avalanche P-Chain RPC URL."""
    avalanche_p_private_rpc_2: str | None = None
    """Secondary private Avalanche P-Chain RPC URL for redundancy."""
    avalanche_p_public_rpcs: list[str] = [
        "https://api.avax.network/ext/P",
        "https://avalanche.publicnode.com",
    ]
    """Public Avalanche P-Chain RPC URLs (tried in order)."""

    avalanche_c_private_rpc_1: str | None = None
    """Private Avalanche C-Chain RPC URL (EVM)."""
    avalanche_c_private_rpc_2: str | None = None
    """Secondary private Avalanche C-Chain RPC URL for redundancy."""
    avalanche_c_public_rpcs: list[str] = [
        "https://api.avax.network/ext/bc/C/rpc",
        "https://avalanche-c-chain-rpc.publicnode.com",
    ]
    """Public Avalanche C-Chain RPC URLs (tried in order)."""

    ink_url: str = "https://rpc-qnd.inkonchain.com"
    """Ink RPC URL supporting ``optimism_syncStatus`` (QuickNode)."""
    unichain_url: str = "https://mainnet.unichain.org"
    """Unichain RPC URL supporting ``optimism_syncStatus`` (PrivateRPC)."""

    @staticmethod
    def _combine(
        private_1: str | None, private_2: str | None, public: list[str]
    ) -> list[str]:
        urls: list[str] = []
        if private_1:
            urls.append(private_1)
        if private_2:
            urls.append(private_2)
        urls.extend(public)
        return urls

    @property
    def ethereum_urls(self) -> list[str]:
        """Combined Ethereum RPC URLs, with private URLs prioritized."""
        return self._combine(
            self.ethereum_private_rpc_1,
            self.ethereum_private_rpc_2,
            self.ethereum_public_rpcs,
        )

    @property
    def arbitrum_urls(self) -> list[str]:
        """Combined Arbitrum RPC URLs, with private URLs prioritized."""
        return self._combine(
            self.arbitrum_private_rpc_1,
            self.arbitrum_private_rpc_2,
            self.arbitrum_public_rpcs,
        )

    @property
    def base_urls(self) -> list[str]:
        """Combined Base RPC URLs, with private URLs prioritized."""
        return self._combine(
            self.base_private_rpc_1,
            self.base_private_rpc_2,
            self.base_public_rpcs,
        )

    @property
    def polygon_urls(self) -> list[str]:
        """Combined Polygon RPC URLs, with private URLs prioritized."""
        return self._combine(
            self.polygon_private_rpc_1,
            self.polygon_private_rpc_2,
            self.polygon_public_rpcs,
        )

    @property
    def optimism_urls(self) -> list[str]:
        """Combined Optimism RPC URLs, with private URLs prioritized."""
        return self._combine(
            self.optimism_private_rpc_1,
            self.optimism_private_rpc_2,
            self.optimism_public_rpcs,
        )

    @property
    def solana_urls(self) -> list[str]:
        """Combined Solana RPC URLs, with private URLs prioritized."""
        return self._combine(
            self.solana_private_rpc_1,
            self.solana_private_rpc_2,
            self.solana_public_rpcs,
        )

    @property
    def avalanche_p_urls(self) -> list[str]:
        """Combined Avalanche P-Chain RPC URLs, with private URLs prioritized."""
        return self._combine(
            self.avalanche_p_private_rpc_1,
            self.avalanche_p_private_rpc_2,
            self.avalanche_p_public_rpcs,
        )

    @property
    def avalanche_c_urls(self) -> list[str]:
        """Combined Avalanche C-Chain (EVM) RPC URLs, with private URLs prioritized."""
        return self._combine(
            self.avalanche_c_private_rpc_1,
            self.avalanche_c_private_rpc_2,
            self.avalanche_c_public_rpcs,
        )


@cache
def get_rpc_settings() -> RPCSettings:
    """Get cached RPCSettings instance."""
    return RPCSettings()
