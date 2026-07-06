// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

/**
 * Maps a market's numeric `chain_id` to a human-friendly display name.
 *
 * EVM chain IDs are positive integers; Solana uses the sentinel `-1`
 * because it has no native integer chain id. Other non-EVM chains can
 * be added with their own negative sentinels.
 *
 * Components should always render markets through `formatChainId`
 * rather than displaying the raw number — the UI should never expose
 * `-1` as Solana's identifier to end users.
 */

const CHAIN_LABELS: Record<number, string> = {
	// Non-EVM (negative sentinels)
	[-1]: "Solana",

	// EVM mainnets
	1: "Ethereum",
	10: "Optimism",
	56: "BNB Smart Chain",
	100: "Gnosis",
	130: "Unichain",
	137: "Polygon",
	324: "zkSync",
	8453: "Base",
	42161: "Arbitrum One",
	43114: "Avalanche C",
	57073: "Ink",
	59144: "Linea",
	534352: "Scroll",
}

/**
 * Return the human-readable label for `chainId`, or a `Chain {id}`
 * fallback when no mapping exists. Adding a new chain to the platform
 * just means adding a row to `CHAIN_LABELS` above — no code change
 * elsewhere.
 */
export function formatChainId(chainId: number): string {
	return CHAIN_LABELS[chainId] ?? `Chain ${chainId}`
}

/**
 * The reverse direction is intentionally not exposed: chain *names* in
 * URLs / API responses would invite typos and case-sensitivity bugs,
 * so we always pass `chain_id` over the wire and label client-side.
 */
