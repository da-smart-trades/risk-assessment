// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

/**
 * Helpers for rendering automated market evidence in the UI.
 *
 * Evidence comes from the yarn collector as a JSON dict keyed by
 * camelCase metric names. The show page converts each key to a
 * human-readable label via `formatEvidenceKey` and dispatches on the
 * value's runtime type via `<EvidenceTree>` (string / number / array /
 * object).
 */

/**
 * Convert a camelCase evidence key to Title Case for display.
 *
 * Examples:
 *   formatEvidenceKey("idleLiquidityRatio") === "Idle Liquidity Ratio"
 *   formatEvidenceKey("USDCRatio")          === "USDC Ratio"
 *   formatEvidenceKey("tvlUSD")             === "Tvl USD"
 *   formatEvidenceKey("id")                 === "Id"
 *
 * Two-pass regex so consecutive uppercase runs stay together as
 * acronyms (`USDC`, `USD`) rather than `U S D C`.
 *
 * Known edge case: leading two-letter acronyms aren't recognised
 * separately — `pdValue` becomes `Pd Value` rather than `PD Value`.
 * Fixing that requires a whitelist of acronyms; deferred until
 * operators surface a confusion.
 */
export function formatEvidenceKey(key: string): string {
	if (!key) return ""
	const withSpaces = key
		// Insert a space between a lowercase / digit and a following uppercase:
		//   "idleLiquidity" → "idle Liquidity"
		.replace(/([a-z0-9])([A-Z])/g, "$1 $2")
		// Split acronym runs from following capitalised words:
		//   "USDCRatio" → "USDC Ratio"
		.replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
	return withSpaces.charAt(0).toUpperCase() + withSpaces.slice(1)
}

/**
 * Convert a market label to SCREAMING_SNAKE_CASE for display.
 *
 * The collector labels markets in PascalCase (e.g. `AaveV3Ethereum`,
 * `aaveV3Base`); operators expect the canonical constant form with the
 * word boundaries made explicit.
 *
 * Examples:
 *   formatMarketLabel("aaveV3Base")     === "AAVE_V3_BASE"
 *   formatMarketLabel("AaveV3Ethereum") === "AAVE_V3_ETHEREUM"
 *   formatMarketLabel("USDCMarket")     === "USDC_MARKET"
 *
 * Boundaries: between a lowercase/digit and a following uppercase, and
 * between an acronym run and a following capitalised word — then any
 * existing separators collapse to a single underscore and the whole
 * string is upper-cased.
 */
export function formatMarketLabel(label: string): string {
	if (!label) return ""
	return label
		.replace(/([a-z0-9])([A-Z])/g, "$1_$2")
		.replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
		.replace(/[\s-]+/g, "_")
		.toUpperCase()
}

/**
 * Format a final-PD value (a probability in 0..1) as a percentage.
 * `MarketScore.final_pd` carries six decimal places of fractional
 * precision; two decimals of percent preserves the same significant
 * precision (e.g. 0.0234 → "2.34%").
 */
export function formatFinalPd(value: number | string | null | undefined): string {
	if (value === null || value === undefined || value === "") return "—"
	const num = typeof value === "number" ? value : Number(value)
	if (Number.isNaN(num)) return "—"
	return `${(num * 100).toFixed(2)}%`
}

/**
 * Format a breakdown term (anchors / control / assurance) for the PD
 * breakdown line. Unlike the PD itself these are raw factors of the
 * product — control and assurance are dimensionless multipliers clamped
 * to [0.75, 1.25] — so they render as plain four-decimal values, not
 * percentages.
 */
export function formatTerm(value: number | string | null | undefined): string {
	if (value === null || value === undefined || value === "") return "—"
	const num = typeof value === "number" ? value : Number(value)
	if (Number.isNaN(num)) return "—"
	return num.toFixed(4)
}
