// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head } from "@inertiajs/react"
import { HelpCircle } from "lucide-react"
import type React from "react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react" // useRef kept for wheelHandlerRef
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"
import { AssetLogo } from "@/components/asset-logo"
import { Container } from "@/components/container"
import { FavoriteButton } from "@/components/favorite-button"
import { Header } from "@/components/header"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { type ChartConfig, ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { AppLayout } from "@/layouts/app-layout"
import type { OperatorSnapshot } from "@/lib/generated/api/types.gen"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { CHAIN_LOGOS } from "@/lib/logos"
import { cn } from "@/lib/utils"

// Selected tab uses brand green text instead of the default gradient-fill pill.
const ACTIVE_TAB_CLASS = "data-[state=active]:bg-transparent data-[state=active]:text-primary data-[state=active]:shadow-none"

const CHAIN_LABELS: Record<string, string> = {
	ARBITRUM: "Arbitrum",
	ETHEREUM: "Ethereum",
	SOLANA: "Solana",
	BASE: "Base",
	INK: "Ink",
	UNICHAIN: "Unichain",
	POLYGON: "Polygon",
	AVALANCHE_C: "Avalanche C",
	OPTIMISM: "Optimism",
	CANTON: "Canton",
}

const CHAIN_FINALITY_METRIC: Record<string, string> = {
	ETHEREUM: "ETH_FINALITY",
	SOLANA: "SOL_FINALITY",
	ARBITRUM: "ARB_FINALITY",
	BASE: "BASE_FINALITY",
	INK: "INK_FINALITY",
	UNICHAIN: "UNICHAIN_FINALITY",
	POLYGON: "POLYGON_FINALITY",
	OPTIMISM: "OPTIMISM_FINALITY",
	CANTON: "CANTON_FINALITY",
}

// Mirrors src/cert_ra/api/domain/favorites/resolver.py::_FINALITY_DETAIL — the
// per-chain explanation copy shown both on the dashboard favorite card and as
// a hover tooltip on the corresponding finality chart titles below.
const L2_FINALITY_EXPLANATION =
	"L2 blocks are 'finalized' when the L1 batch posting reaches L1 Casper finality (~13 min). The safe → finalized depth (primary) is gated by L1 finalization, so persistent growth means the L1 finality path is stalled. Time to hard finality (secondary) expresses the same condition in seconds — a direct wall-clock measure of how long an L2 'safe' block waits before it is finalized via the L1 bridge; under healthy operation it tracks L1 Casper finality."
const OP_STACK_FINALITY_EXPLANATION =
	"OP-stack chain. Finality is L1-gated through output root finalization on Ethereum, so the safe → finalized depth (primary) is the direct signal: persistent growth means L1 finality (and therefore L2 finality) has stalled. Time to hard finality (secondary) is the seconds-based companion: the wall-clock delay between L2 safe inclusion and L1 output root finalization, which under healthy operation tracks L1 Casper finality (~13 min)."
const CANTON_FINALITY_EXPLANATION =
	"Canton finality is deterministic: a transaction is final the instant its BFT-ordered two-phase commit completes, so there is no safe→finalized block gradient. The health signals that can stall are instead (1) cadence/freshness — rounds open on a ~10-minute cycle and the ledger should keep producing updates, so a large 'round advance' or 'ledger freshness' value means the network has stopped advancing; and (2) the SV consensus quorum margin — how many Super Validators could drop before the >2/3 BFT voting threshold can no longer be met (a margin at or below zero means quorum is already at risk)."

const FINALITY_EXPLANATIONS: Record<string, string> = {
	ETHEREUM:
		"Under Casper FFG, finalized = justified - 1 in healthy operation, so the justified → finalized gap (primary) is 1; ≥2 epochs is the on-chain definition of a non-finalizing state. Seconds since the last finality advance (secondary) is the wall-clock view of the same condition — healthy is below ~15 min (a new finalized epoch lands every ~12.8 min). A value well past that confirms the chain has stopped finalizing rather than briefly lagging.",
	SOLANA:
		"Solana finalizes when ≥2/3 stake roots a slot. The confirmed → finalized gap (primary) is normally ~32 slots; persistent growth means supermajority rooting has stalled. The processed → confirmed gap (secondary) is the upstream stage of the same pipeline — a growing value means vote aggregation is lagging before slots even reach the confirmed level, often the earliest indicator that supermajority finalization is about to break.",
	POLYGON:
		"Polygon PoS finalizes via Heimdall checkpoints submitted to Ethereum. The latest → finalized depth (primary) grows when checkpoints aren't being submitted or verified on L1 — the canonical 'finality is stuck' symptom. Seconds since last head (secondary) pairs this with a block-production liveness check: if Bor itself isn't producing new heads, finalization can't progress either, regardless of checkpoint health on the Heimdall/L1 side.",
	ARBITRUM: L2_FINALITY_EXPLANATION,
	BASE: L2_FINALITY_EXPLANATION,
	OPTIMISM: L2_FINALITY_EXPLANATION,
	INK: OP_STACK_FINALITY_EXPLANATION,
	UNICHAIN: OP_STACK_FINALITY_EXPLANATION,
	CANTON: CANTON_FINALITY_EXPLANATION,
}

// Frontend metric keys (camelCase) for the primary/secondary finality columns
// per chain. A chart receives the explanation tooltip when any of its metrics
// fall in this set.
const FINALITY_CHART_KEYS: Record<string, ReadonlySet<string>> = {
	ETHEREUM: new Set(["justifiedFinalizedGap", "timeSinceFinalityAdvance"]),
	SOLANA: new Set(["confirmedFinalizedGap", "processedConfirmedGap"]),
	POLYGON: new Set(["latestToFinalizedBlocks", "timeSinceLastHead"]),
	ARBITRUM: new Set(["safeToFinalizedBlocks", "timeToHardFinality"]),
	BASE: new Set(["safeToFinalizedBlocks", "timeToHardFinality"]),
	OPTIMISM: new Set(["safeToFinalizedBlocks", "timeToHardFinality"]),
	INK: new Set(["safeToFinalizedBlocks", "timeToHardFinality"]),
	UNICHAIN: new Set(["safeToFinalizedBlocks", "timeToHardFinality"]),
	CANTON: new Set(["roundAdvanceSeconds", "ledgerFreshnessSeconds", "svQuorumMargin"]),
}

interface MetricConfig {
	key: string
	label: string
}

interface MetricGroup {
	title: string
	metrics: MetricConfig[]
	// Optional per-chart tooltip text. When set it takes precedence over the
	// chain-level ``chartTooltip`` keyed lookup so individual groups can carry
	// their own copy without touching the central FINALITY_EXPLANATIONS map.
	description?: string
}

interface ChainMetricConfig {
	endpoint: string
	groups: MetricGroup[]
	filterByChain?: boolean
}

type ChainFinalityConfig = ChainMetricConfig

const FINALITY_CONFIG: Partial<Record<string, ChainFinalityConfig>> = {
	ETHEREUM: {
		endpoint: "/metrics/finality/ethereum",
		groups: [
			{
				title: "Block Heights",
				metrics: [
					{ key: "headHeight", label: "Head" },
					{ key: "finalizedHeight", label: "Finalized" },
					{ key: "safeHeight", label: "Safe" },
				],
			},
			{
				title: "Timing (s)",
				metrics: [
					{ key: "headToFinalizedTime", label: "Head→Finalized" },
					{ key: "timeSinceFinalityAdvance", label: "Since Finality Advance" },
				],
			},
			{
				title: "Epoch Data",
				metrics: [
					{ key: "justifiedEpoch", label: "Justified" },
					{ key: "finalizedEpoch", label: "Finalized" },
				],
			},
			{
				title: "Justified-Finalized Gap (epochs)",
				metrics: [{ key: "justifiedFinalizedGap", label: "Gap" }],
			},
		],
	},
	ARBITRUM: {
		endpoint: "/metrics/finality/evm-l2",
		filterByChain: true,
		groups: [
			{
				title: "Block Heights",
				metrics: [
					{ key: "latestHeight", label: "Latest" },
					{ key: "safeHeight", label: "Safe" },
					{ key: "finalizedHeight", label: "Finalized" },
				],
			},
			{
				title: "Block Gaps",
				metrics: [
					{ key: "latestToSafeBlocks", label: "Latest→Safe" },
					{ key: "safeToFinalizedBlocks", label: "Safe→Finalized" },
					{ key: "heightCorrelation", label: "Height Correlation" },
				],
			},
		],
	},
	BASE: {
		endpoint: "/metrics/finality/evm-l2",
		filterByChain: true,
		groups: [
			{
				title: "Block Heights",
				metrics: [
					{ key: "latestHeight", label: "Latest" },
					{ key: "safeHeight", label: "Safe" },
					{ key: "finalizedHeight", label: "Finalized" },
				],
			},
			{
				title: "Block Gaps",
				metrics: [
					{ key: "latestToSafeBlocks", label: "Latest→Safe" },
					{ key: "safeToFinalizedBlocks", label: "Safe→Finalized" },
				],
			},
		],
	},
	OPTIMISM: {
		endpoint: "/metrics/finality/evm-l2",
		filterByChain: true,
		groups: [
			{
				title: "Block Heights",
				metrics: [
					{ key: "latestHeight", label: "Latest" },
					{ key: "safeHeight", label: "Safe" },
					{ key: "finalizedHeight", label: "Finalized" },
				],
			},
			{
				title: "Block Gaps",
				metrics: [
					{ key: "latestToSafeBlocks", label: "Latest→Safe" },
					{ key: "safeToFinalizedBlocks", label: "Safe→Finalized" },
					{ key: "heightCorrelation", label: "Height Correlation" },
				],
			},
		],
	},
	INK: {
		endpoint: "/metrics/finality/op-stack",
		filterByChain: true,
		groups: [
			{
				title: "Block Heights",
				metrics: [
					{ key: "unsafeHeight", label: "Unsafe" },
					{ key: "safeHeight", label: "Safe" },
					{ key: "finalizedHeight", label: "Finalized" },
				],
			},
			{
				title: "Block Gaps",
				metrics: [
					{ key: "unsafeToSafeBlocks", label: "Unsafe→Safe" },
					{ key: "safeToFinalizedBlocks", label: "Safe→Finalized" },
					{ key: "heightCorrelation", label: "Height Correlation" },
				],
			},
			{
				title: "Timing (s)",
				metrics: [
					{ key: "timeSinceLastUnsafe", label: "Since Last Unsafe" },
					{ key: "timeToHardFinality", label: "Hard Finality" },
				],
			},
		],
	},
	UNICHAIN: {
		endpoint: "/metrics/finality/op-stack",
		filterByChain: true,
		groups: [
			{
				title: "Block Heights",
				metrics: [
					{ key: "unsafeHeight", label: "Unsafe" },
					{ key: "safeHeight", label: "Safe" },
					{ key: "finalizedHeight", label: "Finalized" },
				],
			},
			{
				title: "Block Gaps",
				metrics: [
					{ key: "unsafeToSafeBlocks", label: "Unsafe→Safe" },
					{ key: "safeToFinalizedBlocks", label: "Safe→Finalized" },
					{ key: "heightCorrelation", label: "Height Correlation" },
				],
			},
			{
				title: "Timing (s)",
				metrics: [
					{ key: "timeSinceLastUnsafe", label: "Since Last Unsafe" },
					{ key: "timeToHardFinality", label: "Hard Finality" },
				],
			},
		],
	},
	POLYGON: {
		endpoint: "/metrics/finality/polygon",
		groups: [
			{
				title: "Block Heights",
				metrics: [
					{ key: "latestHeight", label: "Latest" },
					{ key: "finalizedHeight", label: "Finalized" },
				],
			},
			{
				title: "Gap & Timing",
				metrics: [
					{ key: "latestToFinalizedBlocks", label: "Latest→Finalized (blocks)" },
					{ key: "timeSinceLastHead", label: "Since Last Head (s)" },
				],
			},
		],
	},
	SOLANA: {
		endpoint: "/metrics/finality/solana",
		groups: [
			{
				title: "Slot Heights",
				metrics: [
					{ key: "processedSlot", label: "Processed" },
					{ key: "confirmedSlot", label: "Confirmed" },
					{ key: "finalizedSlot", label: "Finalized" },
				],
			},
			{
				title: "Slot Gaps",
				metrics: [
					{ key: "confirmedFinalizedGap", label: "Confirmed-Finalized" },
					{ key: "processedConfirmedGap", label: "Processed-Confirmed" },
				],
			},
		],
	},
	// Canton has a single Global Synchronizer (no chain column on the row), so
	// the snapshot is unfiltered. Finality is deterministic; these series track
	// round cadence / ledger freshness and the SV BFT quorum margin instead of
	// block-height gradients.
	CANTON: {
		endpoint: "/metrics/finality/canton",
		groups: [
			{
				title: "Mining Round",
				metrics: [
					{ key: "latestRoundNumber", label: "Latest round" },
					{ key: "openRoundCount", label: "Open rounds" },
				],
			},
			{
				title: "Cadence & Freshness (s)",
				metrics: [
					{ key: "roundAdvanceSeconds", label: "Since round opened" },
					{ key: "roundWindowSeconds", label: "Round window" },
					{ key: "ledgerFreshnessSeconds", label: "Ledger freshness" },
				],
			},
			{
				title: "SV Consensus Quorum",
				metrics: [
					{ key: "liveSvCount", label: "Live SVs" },
					{ key: "votingThreshold", label: "Voting threshold (>2/3)" },
					{ key: "svQuorumMargin", label: "Quorum margin" },
				],
			},
		],
	},
}

// --------------------------------------------------------------------------
// Throughput — gas price, TPS, BPS fetched together from Dune ``transactions``.
// --------------------------------------------------------------------------

const THROUGHPUT_CHAINS = new Set(["ETHEREUM", "ARBITRUM", "SOLANA", "INK", "UNICHAIN", "POLYGON", "AVALANCHE_C", "OPTIMISM", "BASE", "CANTON"])

const THROUGHPUT_GROUPS: MetricGroup[] = [
	{ title: "Gas Price", metrics: [{ key: "gasPrice", label: "Gas Price" }] },
	{ title: "Transactions / sec", metrics: [{ key: "transactionsPerSecond", label: "TPS" }] },
	{ title: "Blocks / sec", metrics: [{ key: "blocksPerSecond", label: "BPS" }] },
]

// Canton has no gas or blocks; the shared throughput row is reused with Canton
// semantics: amulet price (USD/CC) ← gasPrice, updates/sec ← TPS, rounds/sec ←
// BPS. Same endpoint and table; only the labels differ.
const CANTON_THROUGHPUT_GROUPS: MetricGroup[] = [
	{
		title: "Amulet Price (USD / CC)",
		metrics: [{ key: "gasPrice", label: "Amulet price" }],
		description: "Conversion rate from the latest open mining round (amuletPrice): US dollars per Canton Coin.",
	},
	{
		title: "Updates / sec",
		metrics: [{ key: "transactionsPerSecond", label: "Updates/s" }],
		description:
			"Ledger updates (transactions) per second, counted from the bulk /v2/updates stream over a recent window. A -1 value means the window count was unavailable; a flat ceiling means the window hit the page-size cap and the value is a floor.",
	},
	{
		title: "Rounds / sec",
		metrics: [{ key: "blocksPerSecond", label: "Rounds/s" }],
		description: "Economic mining rounds per second (Canton's native time unit; rounds open every ~10 minutes ≈ 0.00167/s).",
	},
]

// --------------------------------------------------------------------------
// Governance — proposal / execution / emergency event counts per chain.
// One DB row per (chain, event_type) poll, so each event_type is rendered as
// its own MetricChart with the raw row's ``count`` as the single series key.
// --------------------------------------------------------------------------

interface GovernanceEventTypeConfig {
	key: string
	label: string
	// "chart" renders a time-series of the count column; "card" shows only the
	// latest value as a compact card, similar to a manual metric.
	display?: "chart" | "card"
	// Optional one-line explanation shown as a hover-tooltip on the card or
	// chart title — useful for clarifying what the count means.
	description?: string
}

const GOVERNANCE_EVENT_TYPES: Partial<Record<string, GovernanceEventTypeConfig[]>> = {
	ETHEREUM: [
		{
			key: "confirmed_eips",
			label: "EIPs confirmed for next hardfork",
			display: "chart",
			description: "Distinct EIP-N references in the next-hardfork meta-EIP markdown. Trends upward as ACD adds EIPs to the upgrade.",
		},
		{
			key: "last_call_eips",
			label: "EIPs at Last Call",
			display: "card",
			description: "Count of EIPs in ethereum/EIPs whose frontmatter status is 'Last Call' — the final review stage before becoming Final.",
		},
	],
	ARBITRUM: [
		{ key: "proposals", label: "Forum proposal topics" },
		{ key: "execution", label: "Timelock CallScheduled/CallExecuted" },
		{ key: "emergency", label: "Security Council UpgradeExecutor events" },
	],
	BASE: [{ key: "execution", label: "UpgradeExecutor events" }],
	SOLANA: [{ key: "proposals", label: "Open SIMD PRs" }],
}

const MM_GOVERNANCE_CATEGORY = "GOVERNANCE"

// --------------------------------------------------------------------------
// Time to finality — average seconds between new heads/slots (soft finality).
// --------------------------------------------------------------------------

const TIME_TO_FINALITY_CHAINS = new Set(["ETHEREUM", "BASE", "INK", "UNICHAIN", "SOLANA"])

const TIME_TO_FINALITY_GROUPS: MetricGroup[] = [
	{
		title: "Soft finality (seconds)",
		metrics: [{ key: "softFinalitySeconds", label: "Mean head/slot interval" }],
	},
]

// L2 timing series read from the per-chain finality_evm_l2 snapshots — split into
// two separate charts so users can read the two signals independently:
// ``timeSinceLastHead`` is a sequencer-liveness check (is the L2 producing
// blocks?), while ``timeToHardFinality`` is an L1-finality check (are L2 safe
// blocks being finalized through the L1 bridge on schedule?). Base does not
// expose a hard-finality timestamp, so it only carries the liveness chart.
const L2_TIMING_TIME_SINCE_LAST_HEAD: MetricGroup = {
	title: "Time since last head (seconds)",
	metrics: [{ key: "timeSinceLastHead", label: "Since last head" }],
	description:
		"Wall-clock seconds since the L2 produced its most recent block. This is a sequencer-liveness signal: under healthy operation it stays at roughly one block-time (~0.25s on Arbitrum, ~2s on Base/Optimism). A persistently rising value means the L2 sequencer has stopped producing blocks, regardless of L1 finality state.",
}

const L2_TIMING_TIME_TO_HARD_FINALITY: MetricGroup = {
	title: "Time to hard finality (seconds)",
	metrics: [{ key: "timeToHardFinality", label: "Latest → finalized delay" }],
	description:
		"Wall-clock seconds between the latest L2 block and the most recent L2 block that has reached hard finality through L1. Hard finality is gated by L1 Casper finalization of the batch posting (~13 min), so healthy operation hovers near that bound. A persistently growing value means the L1 finality path has stalled — batches aren't being finalized on L1 — even if the L2 itself is still producing blocks.",
}

const L2_TIMING_CONFIG: Partial<Record<string, ChainMetricConfig>> = {
	ARBITRUM: {
		endpoint: "/metrics/finality/evm-l2",
		filterByChain: true,
		groups: [L2_TIMING_TIME_SINCE_LAST_HEAD, L2_TIMING_TIME_TO_HARD_FINALITY],
	},
	BASE: {
		endpoint: "/metrics/finality/evm-l2",
		filterByChain: true,
		groups: [L2_TIMING_TIME_SINCE_LAST_HEAD],
	},
	OPTIMISM: {
		endpoint: "/metrics/finality/evm-l2",
		filterByChain: true,
		groups: [L2_TIMING_TIME_SINCE_LAST_HEAD, L2_TIMING_TIME_TO_HARD_FINALITY],
	},
}

// --------------------------------------------------------------------------
// Decentralization — 12 metrics derived from a single validator-stake sample.
// --------------------------------------------------------------------------

const DECENTRALIZATION_CHAINS = new Set(["ETHEREUM", "SOLANA", "POLYGON", "AVALANCHE_C"])

// Canton SVs vote with equal (one-SV-one-vote) BFT power, so the stake-weighted
// HHI/Shapley/Rényi measures don't apply. Decentralization is the count-based
// governance Nakamoto coefficient, served from a dedicated endpoint.
const CANTON_DECENTRALIZATION_ENDPOINT = "/metrics/decentralization/canton"

const CANTON_DECENTRALIZATION_GROUPS: MetricGroup[] = [
	{
		title: "Super Validators",
		metrics: [
			{ key: "svCount", label: "SV count (N)" },
			{ key: "votingThreshold", label: "Voting threshold (>2/3)" },
		],
	},
	{
		title: "Governance Nakamoto",
		metrics: [
			{ key: "govNakamotoSafety", label: "Safety ⌊N/3⌋+1" },
			{ key: "govNakamotoLiveness", label: "Liveness N−thr+1" },
		],
		description:
			"Governance Nakamoto coefficients for the equal-vote SV BFT set. Safety = the minimum SVs that must collude to block a >2/3 governance vote (⌊N/3⌋+1). Liveness = the minimum SVs whose outage stalls governance (N − voting threshold + 1). Higher is more decentralized.",
	},
	{
		title: "Network Size",
		metrics: [
			{ key: "validatorCount", label: "Validators" },
			{ key: "distinctSequencerCount", label: "Sequencers" },
		],
	},
]

const DECENTRALIZATION_GROUPS: MetricGroup[] = [
	{
		title: "Stake & Nodes",
		metrics: [
			{ key: "totalAmountOfStakes", label: "Total stake" },
			{ key: "numberOfNodes", label: "Nodes" },
		],
	},
	{
		title: "Nakamoto Coefficients",
		metrics: [
			{ key: "nakamotoLivenessCoefficient", label: "Liveness (33%)" },
			{ key: "nakamotoSafetyCoefficient", label: "Safety (66%)" },
		],
	},
	{
		title: "Concentration (HHI)",
		metrics: [{ key: "hhi", label: "HHI" }],
	},
	{
		title: "Shapley (top 3)",
		metrics: [
			{ key: "shapleyTopValue", label: "1st" },
			{ key: "shapleySecondValue", label: "2nd" },
			{ key: "shapleyThirdValue", label: "3rd" },
		],
	},
	{
		title: "Renyi Entropy",
		metrics: [
			{ key: "renyiEntropyAlpha0", label: "α=0" },
			{ key: "renyiEntropyAlpha1", label: "α=1" },
			{ key: "renyiEntropyAlpha2", label: "α=2" },
			{ key: "renyiEntropyAlphaInf", label: "α=∞" },
		],
	},
]

// ─── Manual metric category tabs ─────────────────────────────────────────────

interface MmItem {
	id: string
	name: string
	desc: string
	category: string
	subCategory?: string | null
	value?: string | null
	notes?: string | null
	riskScore?: number | null
}

const MM_CATEGORY_LABELS: Record<string, string> = {
	NETWORK: "Network",
	CONSENSUS: "Consensus",
	GOVERNANCE: "Governance",
	TOKEN_RISK: "Token Risk",
	ASSETS_ACTIVITY: "Assets Activity",
}
const MM_CATEGORY_ORDER = ["NETWORK", "CONSENSUS", "GOVERNANCE", "TOKEN_RISK", "ASSETS_ACTIVITY"]
const MM_RISK_STYLES: Record<number, string> = {
	1: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100",
	2: "bg-lime-100 text-lime-900 dark:bg-lime-900/40 dark:text-lime-100",
	3: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100",
	4: "bg-orange-100 text-orange-900 dark:bg-orange-900/40 dark:text-orange-100",
	5: "bg-rose-100 text-rose-900 dark:bg-rose-900/40 dark:text-rose-100",
}

const CHART_COLORS = ["var(--color-chart-1)", "var(--color-chart-2)", "var(--color-chart-3)"]

const INTERVALS: Record<string, number> = {
	"1h": 60 * 60 * 1000,
	"6h": 6 * 60 * 60 * 1000,
	"24h": 24 * 60 * 60 * 1000,
	"7d": 7 * 24 * 60 * 60 * 1000,
	"30d": 30 * 24 * 60 * 60 * 1000,
}

// Format a Date as "YYYY-MM-DDTHH:MM" for datetime-local inputs
function toLocalInput(d: Date): string {
	return d.toISOString().slice(0, 16)
}

type Snapshot = Record<string, unknown>

function useMetricData(chain: string, config: ChainMetricConfig | undefined, fromDate: string, toDate: string) {
	const [snapshots, setSnapshots] = useState<Snapshot[]>([])
	const [loading, setLoading] = useState(true)
	const [error, setError] = useState<string | null>(null)
	const [tick, setTick] = useState(0)
	const prevKeyRef = useRef("")

	// Periodic silent refresh every 5 seconds
	useEffect(() => {
		const id = setInterval(() => setTick((t) => t + 1), 5000)
		return () => clearInterval(id)
	}, [])

	// biome-ignore lint/correctness/useExhaustiveDependencies: tick is intentionally here to drive periodic refreshes
	useEffect(() => {
		if (!config) {
			setLoading(false)
			return
		}

		// Only show the loading spinner when fetch params actually change (not on tick)
		const newKey = `${chain}|${config.endpoint}|${fromDate}|${toDate}`
		const paramChanged = prevKeyRef.current !== newKey
		prevKeyRef.current = newKey

		if (paramChanged) {
			setLoading(true)
			setError(null)
		}

		const controller = new AbortController()
		const params = new URLSearchParams({ pageSize: "1000" })
		if (fromDate) params.set("createdAfter", new Date(fromDate).toISOString())
		if (toDate) params.set("createdBefore", new Date(toDate).toISOString())
		if (config.filterByChain) params.set("chain", chain)

		fetch(`${config.endpoint}?${params}`, { signal: controller.signal })
			.then(async (res) => {
				if (!res.ok) throw new Error(`HTTP ${res.status}`)
				// Tolerate empty or non-JSON bodies (e.g. an endpoint whose table has not
				// been migrated yet) by treating them as "no data" rather than surfacing
				// a parse error to the user.
				try {
					const data = (await res.json()) as { items?: Snapshot[] } | null
					return data?.items ?? []
				} catch {
					return []
				}
			})
			.then((items) => {
				setSnapshots([...items].reverse())
			})
			.catch((err: Error) => {
				if (err.name !== "AbortError") setError(err.message)
			})
			.finally(() => {
				if (!controller.signal.aborted) setLoading(false)
			})

		return () => controller.abort()
	}, [chain, config, fromDate, toDate, tick])

	return { snapshots, loading, error }
}

function IntervalSelector({ value, onChange }: { value: string; onChange: (v: string) => void }) {
	const options = ["1h", "6h", "24h", "7d", "30d", "all"] as const
	return (
		<div className="flex gap-1 justify-end mt-2">
			{options.map((opt) => (
				<button
					key={opt}
					type="button"
					onClick={() => onChange(opt)}
					className={cn(
						"px-2 py-0.5 text-xs rounded transition-colors",
						value === opt ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-muted",
					)}
				>
					{opt === "all" ? "All" : opt}
				</button>
			))}
		</div>
	)
}

function DateRangePicker({ from, to, onChange }: { from: string; to: string; onChange: (from: string, to: string) => void }) {
	return (
		<div className="flex flex-wrap items-center gap-2 text-xs">
			<span className="text-muted-foreground">From</span>
			<input
				type="datetime-local"
				value={from}
				onChange={(e) => onChange(e.target.value, to)}
				className="rounded border border-input bg-background px-2 py-1 text-xs text-foreground [color-scheme:light] dark:[color-scheme:dark]"
			/>
			<span className="text-muted-foreground">To</span>
			<input
				type="datetime-local"
				value={to}
				onChange={(e) => onChange(from, e.target.value)}
				placeholder="now"
				className="rounded border border-input bg-background px-2 py-1 text-xs text-foreground [color-scheme:light] dark:[color-scheme:dark]"
			/>
			{(from || to) && (
				<button type="button" onClick={() => onChange("", "")} className="text-xs text-muted-foreground hover:text-foreground transition-colors">
					Clear
				</button>
			)}
		</div>
	)
}

// Zoom view: a contiguous slice of ``chartData`` rendered on the X axis.
// ``null`` means "show everything" (full live tail).
type ZoomView = { start: number; size: number } | null

// Wheel-zoom tuning. Each notch shrinks/grows the visible window by ~15% and
// the floor of 5 points keeps charts from collapsing into a single tick.
const ZOOM_IN_FACTOR = 0.85
const ZOOM_OUT_FACTOR = 1 / ZOOM_IN_FACTOR
const MIN_VIEW_SIZE = 5

function MetricChart({ title, metrics, snapshots, description }: { title: string; metrics: MetricConfig[]; snapshots: Snapshot[]; description?: string }) {
	const [interval, setInterval] = useState("all")
	const [view, setView] = useState<ZoomView>(null)
	// Stable refs so the wheel logic is always current without replacing the listener.
	const wheelHandlerRef = useRef<((e: WheelEvent) => void) | null>(null)
	const containerElRef = useRef<HTMLDivElement | null>(null)

	const filtered = useMemo(() => {
		if (interval === "all") return snapshots
		const ms = INTERVALS[interval]
		const cutoff = Date.now() - ms
		return snapshots.filter((s) => {
			const t = new Date(s.createdAt as string).getTime()
			return !Number.isNaN(t) && t >= cutoff
		})
	}, [snapshots, interval])

	// Most recent ``createdAt`` across all snapshots — surfaces data freshness
	// regardless of the chart's current interval / zoom window.
	const lastSampledAt = useMemo<Date | null>(() => {
		let maxMs = 0
		for (const s of snapshots) {
			const t = new Date(s.createdAt as string).getTime()
			if (!Number.isNaN(t) && t > maxMs) maxMs = t
		}
		return maxMs > 0 ? new Date(maxMs) : null
	}, [snapshots])

	const chartConfig: ChartConfig = useMemo(() => Object.fromEntries(metrics.map((m, i) => [m.key, { label: m.label, color: CHART_COLORS[i] }])), [metrics])

	const chartData = useMemo(
		() =>
			filtered.map((s) => ({
				time: new Date(s.createdAt as string).toLocaleString(),
				...Object.fromEntries(metrics.map((m) => [m.key, (s[m.key] as number) ?? null])),
			})),
		[filtered, metrics],
	)

	// biome-ignore lint/correctness/useExhaustiveDependencies: interval change is the trigger, not a dep value read inside
	useEffect(() => {
		setView(null)
	}, [interval])

	// Slice chartData for the zoom viewport; null = show all (live tail).
	const visibleData = useMemo(() => {
		if (view === null) return chartData
		return chartData.slice(view.start, view.start + view.size)
	}, [chartData, view])

	// Keep wheel logic up-to-date without replacing the DOM listener.
	useEffect(() => {
		wheelHandlerRef.current = (e: WheelEvent) => {
			const total = chartData.length
			if (total <= MIN_VIEW_SIZE) return
			e.preventDefault()

			const el = containerElRef.current
			if (!el) return
			const rect = el.getBoundingClientRect()
			if (rect.width <= 0) return
			// Cursor x as a fraction of the chart width (0 = left, 1 = right). The
			// data index under the cursor is the zoom anchor — that timestamp stays
			// pinned under the cursor as we shrink/grow the window.
			const cursorX = Math.max(0, Math.min(rect.width, e.clientX - rect.left))
			const cursorFrac = cursorX / rect.width

			setView((prev) => {
				const cur = prev ?? { start: 0, size: total }
				const factor = e.deltaY < 0 ? ZOOM_IN_FACTOR : ZOOM_OUT_FACTOR
				const newSize = Math.max(MIN_VIEW_SIZE, Math.min(total, Math.round(cur.size * factor)))

				// Fully zoomed out → drop back to "show everything".
				if (newSize >= total) return null
				if (newSize === cur.size) return cur

				const idxUnderCursor = cur.start + cursorFrac * cur.size
				const rawStart = Math.round(idxUnderCursor - cursorFrac * newSize)
				const newStart = Math.max(0, Math.min(total - newSize, rawStart))
				return { start: newStart, size: newSize }
			})
		}
	}, [chartData.length])

	// Callback ref: attaches the wheel listener the moment the chart div mounts
	// (fixes the race where an empty-deps useEffect runs before data arrives and the div exists)
	const containerRef = useCallback((el: HTMLDivElement | null) => {
		containerElRef.current = el
		if (!el) return
		const handler = (e: WheelEvent) => wheelHandlerRef.current?.(e)
		el.addEventListener("wheel", handler, { passive: false })
		return () => el.removeEventListener("wheel", handler)
	}, [])

	const handleDoubleClick = useCallback(() => setView(null), [])

	return (
		<div className="space-y-1">
			<div className="flex items-center justify-between">
				<div className="flex items-center gap-1">
					<p className="text-sm font-medium text-foreground">{title}</p>
					{description && (
						<Tooltip>
							<TooltipTrigger asChild>
								<button
									type="button"
									aria-label="About this metric"
									className="inline-flex h-4 w-4 items-center justify-center text-muted-foreground transition-colors hover:text-foreground"
								>
									<HelpCircle className="h-3.5 w-3.5" />
								</button>
							</TooltipTrigger>
							<TooltipContent className="max-w-80 text-xs leading-relaxed">{description}</TooltipContent>
						</Tooltip>
					)}
				</div>
				<div className="flex items-center gap-3 text-xs text-muted-foreground">
					{lastSampledAt && <span title={lastSampledAt.toISOString()}>Last sampled: {lastSampledAt.toLocaleString()}</span>}
					{view !== null && (
						<button type="button" onClick={() => setView(null)} className="hover:text-foreground transition-colors">
							↩ Reset zoom
						</button>
					)}
				</div>
			</div>
			{chartData.length === 0 ? (
				<div className="flex h-44 items-center justify-center rounded-lg border border-border text-muted-foreground text-sm">No data for this interval</div>
			) : (
				<div ref={containerRef} role="img" aria-label={title} onDoubleClick={handleDoubleClick} className="select-none cursor-crosshair">
					<ChartContainer config={chartConfig} className="h-44 w-full">
						<AreaChart data={visibleData} margin={{ top: 4, right: 8, left: 4, bottom: 0 }}>
							<CartesianGrid strokeDasharray="3 3" stroke="var(--color-muted-foreground)" strokeOpacity={0.15} />
							<XAxis
								dataKey="time"
								tick={{ fontSize: 9, fill: "var(--color-muted-foreground)" }}
								tickLine={false}
								axisLine={{ stroke: "var(--color-muted-foreground)", strokeOpacity: 0.35 }}
								interval="equidistantPreserveStart"
							/>
							<YAxis tick={{ fontSize: 9, fill: "var(--color-muted-foreground)" }} tickLine={false} axisLine={false} width={56} />
							<ChartTooltip content={<ChartTooltipContent />} />
							<ChartLegend content={<ChartLegendContent />} />
							{metrics.map((m, i) => (
								<Area key={m.key} type="monotone" dataKey={m.key} stroke={CHART_COLORS[i]} fill={CHART_COLORS[i]} fillOpacity={0.15} dot={false} connectNulls={false} />
							))}
						</AreaChart>
					</ChartContainer>
				</div>
			)}
			<IntervalSelector value={interval} onChange={setInterval} />
		</div>
	)
}

// ─── Operator panel ──────────────────────────────────────────────────────────
// Renders on each chain's decentralization tab once an operator snapshot is
// persisted. Data sources differ per chain (Rated for Ethereum, the native
// staking API for Polygon, P-Chain RPC for Avalanche, getVoteAccounts for
// Solana — all augmented by curated labels where useful).

const OPERATOR_PANEL_CHAINS = new Set(["ETHEREUM", "SOLANA", "POLYGON", "AVALANCHE_C"])

const OPERATOR_STAKE_UNITS: Record<string, string> = {
	ETHEREUM: "ETH",
	SOLANA: "SOL",
	POLYGON: "POL",
	AVALANCHE_C: "AVAX",
}

const OPERATOR_SOURCE_LABELS: Record<string, string> = {
	ETHEREUM: "Rated Network",
	SOLANA: "Solana RPC + curated labels",
	POLYGON: "Polygon Staking API",
	AVALANCHE_C: "Avalanche P-Chain + curated labels",
}

const OPERATOR_STAKE_FORMATTER = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 })
const OPERATOR_PERCENT_FORMATTER = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 })

function useOperatorSnapshot(chain: string) {
	const [data, setData] = useState<OperatorSnapshot | null>(null)
	const [loading, setLoading] = useState(true)
	const [error, setError] = useState<string | null>(null)

	useEffect(() => {
		const controller = new AbortController()
		setLoading(true)
		setError(null)
		setData(null)

		const params = new URLSearchParams({ chain, pageSize: "1" })
		fetch(`/metrics/decentralization/operators?${params}`, { signal: controller.signal })
			.then(async (res) => {
				if (!res.ok) throw new Error(`HTTP ${res.status}`)
				try {
					const body = (await res.json()) as { items?: OperatorSnapshot[] } | null
					return body?.items?.[0] ?? null
				} catch {
					return null
				}
			})
			.then((snapshot) => setData(snapshot))
			.catch((err: Error) => {
				if (err.name !== "AbortError") setError(err.message)
			})
			.finally(() => {
				if (!controller.signal.aborted) setLoading(false)
			})

		return () => controller.abort()
	}, [chain])

	return { data, loading, error }
}

function OperatorPanel({ chain }: { chain: string }) {
	const { data, loading, error } = useOperatorSnapshot(chain)

	if (loading) {
		return (
			<Card>
				<CardHeader>
					<CardTitle>Top Operators</CardTitle>
				</CardHeader>
				<CardContent className="text-muted-foreground text-sm">Loading operator data…</CardContent>
			</Card>
		)
	}

	if (error) {
		return (
			<Card>
				<CardHeader>
					<CardTitle>Top Operators</CardTitle>
				</CardHeader>
				<CardContent className="text-destructive text-sm">Failed to load operator data: {error}</CardContent>
			</Card>
		)
	}

	if (!data) {
		return (
			<Card>
				<CardHeader>
					<CardTitle>Top Operators</CardTitle>
				</CardHeader>
				<CardContent className="text-muted-foreground text-sm">
					No operator snapshot yet — the daily refresh writes the first row after the Rated API key is configured.
				</CardContent>
			</Card>
		)
	}

	const updated = new Date(data.createdAt)
	const stakeUnit = OPERATOR_STAKE_UNITS[chain] ?? "stake"
	const source = OPERATOR_SOURCE_LABELS[chain] ?? "—"

	return (
		<Card>
			<CardHeader className="space-y-2">
				<div className="flex flex-wrap items-center justify-between gap-2">
					<CardTitle>Top Operators</CardTitle>
					<span className="text-muted-foreground text-xs">
						Updated {updated.toLocaleString()} · {source}
					</span>
				</div>
				<div className="flex flex-wrap gap-2 text-xs">
					<Badge variant="secondary">
						Entity Nakamoto liveness: <span className="ml-1 tabular-nums">{data.entityNakamotoLiveness}</span>
					</Badge>
					<Badge variant="secondary">
						Entity Nakamoto safety: <span className="ml-1 tabular-nums">{data.entityNakamotoSafety}</span>
					</Badge>
					<Badge variant="secondary">
						Labeled entities: <span className="ml-1 tabular-nums">{data.entityCount}</span>
					</Badge>
					<Badge variant="outline">Coverage {OPERATOR_PERCENT_FORMATTER.format(data.coveragePct)}</Badge>
				</div>
			</CardHeader>
			<CardContent>
				<table className="w-full text-sm">
					<thead className="text-muted-foreground text-xs uppercase">
						<tr>
							<th className="py-2 pr-4 text-left font-medium">#</th>
							<th className="py-2 pr-4 text-left font-medium">Operator</th>
							<th className="py-2 pr-4 text-right font-medium">Validators</th>
							<th className="py-2 pr-4 text-right font-medium">Stake ({stakeUnit})</th>
							<th className="py-2 text-right font-medium">Share</th>
						</tr>
					</thead>
					<tbody>
						{data.topOperators.map((op) => (
							<tr key={op.operatorId} className="border-t border-border/40">
								<td className="py-2 pr-4 text-muted-foreground tabular-nums">{op.rank}</td>
								<td className="py-2 pr-4 font-medium">{op.name}</td>
								<td className="py-2 pr-4 text-right tabular-nums">{OPERATOR_STAKE_FORMATTER.format(op.validatorCount)}</td>
								<td className="py-2 pr-4 text-right tabular-nums">{OPERATOR_STAKE_FORMATTER.format(op.stake)}</td>
								<td className="py-2 text-right tabular-nums">{OPERATOR_PERCENT_FORMATTER.format(op.stakeShare)}</td>
							</tr>
						))}
					</tbody>
				</table>
			</CardContent>
		</Card>
	)
}

function MetricsTab({
	chain,
	config,
	emptyMessage,
	errorPrefix,
	fromDate,
	toDate,
	onRangeChange,
	title,
	favoriteMetricType,
	chartTooltip,
}: {
	chain: string
	config: ChainMetricConfig | undefined
	emptyMessage: string
	errorPrefix: string
	fromDate: string
	toDate: string
	onRangeChange: (from: string, to: string) => void
	title?: string
	favoriteMetricType?: string
	chartTooltip?: { keys: ReadonlySet<string>; text: string }
}) {
	const { snapshots, loading, error } = useMetricData(chain, config, fromDate, toDate)

	if (!config) {
		return <p className="text-muted-foreground text-sm">{emptyMessage}</p>
	}

	return (
		<div className="space-y-6">
			<div className="flex items-center justify-between gap-2">
				{title ? (
					<div className="flex items-center gap-1">
						<h2 className="text-lg font-semibold">{title}</h2>
						{favoriteMetricType && <FavoriteButton size="sm" target={{ kind: "auto", metricType: favoriteMetricType, chain }} />}
					</div>
				) : (
					<div />
				)}
				<DateRangePicker from={fromDate} to={toDate} onChange={onRangeChange} />
			</div>

			{loading && <div className="flex h-44 items-center justify-center text-muted-foreground text-sm">Loading…</div>}
			{!loading && error && (
				<p className="text-destructive text-sm">
					{errorPrefix}: {error}
				</p>
			)}

			{!loading && !error && (
				<div className="grid grid-cols-1 gap-8">
					{config.groups.map((group) => {
						const hasTooltipKey = chartTooltip ? group.metrics.some((m) => chartTooltip.keys.has(m.key)) : false
						const description = group.description ?? (hasTooltipKey ? chartTooltip?.text : undefined)
						return <MetricChart key={group.title} title={group.title} metrics={group.metrics} snapshots={snapshots} description={description} />
					})}
				</div>
			)}
		</div>
	)
}

function GovernanceTab({
	chain,
	eventTypes,
	manualItems,
	manualLoading,
	fromDate,
	toDate,
	onRangeChange,
}: {
	chain: string
	eventTypes: GovernanceEventTypeConfig[] | undefined
	manualItems: MmItem[]
	manualLoading: boolean
	fromDate: string
	toDate: string
	onRangeChange: (from: string, to: string) => void
}) {
	const config = useMemo<ChainMetricConfig>(() => ({ endpoint: "/metrics/governance", groups: [], filterByChain: true }), [])
	// Only fetch the auto event series when this chain has any auto event types.
	const { snapshots, loading, error } = useMetricData(chain, eventTypes ? config : undefined, fromDate, toDate)

	const byEventType = useMemo(() => {
		const map: Record<string, Snapshot[]> = {}
		for (const s of snapshots) {
			const et = s.eventType as string | undefined
			if (!et) continue
			const list = map[et] ?? []
			list.push(s)
			map[et] = list
		}
		return map
	}, [snapshots])

	const chartEventTypes = useMemo(() => (eventTypes ?? []).filter((et) => (et.display ?? "chart") === "chart"), [eventTypes])
	const cardEventTypes = useMemo(() => (eventTypes ?? []).filter((et) => et.display === "card"), [eventTypes])
	const hasAuto = Boolean(eventTypes && eventTypes.length > 0)

	return (
		<div className="space-y-8">
			<div className="flex items-center justify-between gap-2">
				<h2 className="text-lg font-semibold">Governance</h2>
				{hasAuto && <DateRangePicker from={fromDate} to={toDate} onChange={onRangeChange} />}
			</div>

			{hasAuto && (
				<div className="space-y-6">
					{loading && <div className="flex h-44 items-center justify-center text-muted-foreground text-sm">Loading…</div>}
					{!loading && error && <p className="text-destructive text-sm">Failed to load governance data: {error}</p>}

					{!loading && !error && (
						<>
							{cardEventTypes.length > 0 && (
								<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
									{cardEventTypes.map((et) => (
										<GovernanceEventCard key={et.key} event={et} snapshots={byEventType[et.key] ?? []} />
									))}
								</div>
							)}
							{chartEventTypes.length > 0 && (
								<div className="grid grid-cols-1 gap-8">
									{chartEventTypes.map((et) => (
										<MetricChart
											key={et.key}
											title={et.label}
											metrics={[{ key: "count", label: "Events / poll" }]}
											snapshots={byEventType[et.key] ?? []}
											description={et.description}
										/>
									))}
								</div>
							)}
						</>
					)}
				</div>
			)}

			{(manualItems.length > 0 || manualLoading) && (
				<div className="space-y-3">
					{hasAuto && <Separator />}
					<h3 className="text-base font-semibold text-muted-foreground">Manual governance metrics</h3>
					<ManualMetricCategoryContent items={manualItems} loading={manualLoading} />
				</div>
			)}
		</div>
	)
}

function GovernanceEventCard({ event, snapshots }: { event: GovernanceEventTypeConfig; snapshots: Snapshot[] }) {
	const latest = snapshots.length > 0 ? snapshots[snapshots.length - 1] : null
	const value = latest?.count
	const displayValue = typeof value === "number" ? value.toLocaleString() : "—"
	return (
		<Card>
			<CardHeader className="pb-3">
				<div className="flex items-start gap-1.5">
					<CardTitle className="text-base font-semibold leading-tight">{event.label}</CardTitle>
					{event.description && (
						<Tooltip>
							<TooltipTrigger asChild>
								<button
									type="button"
									className="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-muted-foreground/30 text-[10px] font-semibold text-muted-foreground transition-colors hover:border-foreground/60 hover:text-foreground"
								>
									?
								</button>
							</TooltipTrigger>
							<TooltipContent className="max-w-64 text-xs">{event.description}</TooltipContent>
						</Tooltip>
					)}
				</div>
			</CardHeader>
			<Separator />
			<CardContent className="pt-3">
				<span className="text-2xl font-semibold tabular-nums">{displayValue}</span>
			</CardContent>
		</Card>
	)
}

function toTitleCase(str: string): string {
	return str
		.replace(/_/g, " ")
		.toLowerCase()
		.replace(/\b\w/g, (c) => c.toUpperCase())
}

function groupMmBySub(items: MmItem[]) {
	const map = new Map<string | null, MmItem[]>()
	for (const m of items) {
		const key = m.subCategory ?? null
		const existing = map.get(key)
		if (existing) {
			existing.push(m)
		} else {
			map.set(key, [m])
		}
	}
	return Array.from(map.entries())
		.sort(([a], [b]) => {
			if (a === null && b === null) return 0
			if (a === null) return 1
			if (b === null) return -1
			return a.localeCompare(b)
		})
		.map(([sub, subItems]) => ({ sub, items: subItems }))
}

function ManualMetricCategoryContent({ items, loading }: { items: MmItem[]; loading: boolean }) {
	if (loading) {
		return <p className="text-muted-foreground text-sm">Loading manual metrics…</p>
	}
	if (items.length === 0) {
		return <p className="text-muted-foreground text-sm">No manual metrics published for this category on this chain yet.</p>
	}
	const subGroups = groupMmBySub(items)
	return (
		<div className="space-y-6">
			{subGroups.map(({ sub, items: subItems }) => (
				<div key={sub ?? "__root__"}>
					{sub && <h4 className="mb-3 text-base font-semibold">{toTitleCase(sub)}</h4>}
					<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
						{subItems.map((m) => (
							<Card key={m.id}>
								<CardHeader className="pb-3">
									<div className="flex items-start gap-1.5">
										<CardTitle className="text-base font-semibold leading-tight">{m.name}</CardTitle>
										<Tooltip>
											<TooltipTrigger asChild>
												<button
													type="button"
													className="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-muted-foreground/30 text-[10px] font-semibold text-muted-foreground transition-colors hover:border-foreground/60 hover:text-foreground"
												>
													?
												</button>
											</TooltipTrigger>
											<TooltipContent className="max-w-64 text-xs">{m.desc}</TooltipContent>
										</Tooltip>
									</div>
								</CardHeader>
								{(m.value != null || m.riskScore != null || m.notes) && (
									<>
										<Separator />
										<CardContent className="space-y-1.5 pt-3">
											{(m.value != null || m.riskScore != null) && (
												<div className="flex items-center justify-between gap-2">
													<span className="text-sm font-medium tabular-nums text-muted-foreground">{m.value ?? ""}</span>
													{m.riskScore != null && <Badge className={MM_RISK_STYLES[m.riskScore]}>Risk {m.riskScore}</Badge>}
												</div>
											)}
											{m.notes && <p className="text-muted-foreground text-xs">{m.notes}</p>}
										</CardContent>
									</>
								)}
							</Card>
						))}
					</div>
				</div>
			))}
		</div>
	)
}

function makeFilteredConfig(endpoint: string, groups: MetricGroup[]): ChainMetricConfig {
	return { endpoint, groups, filterByChain: true }
}

// Deep-link query-string keys. Kept short so shared links stay readable.
const URL_PARAM_TAB = "tab"
const URL_PARAM_FROM = "from"
const URL_PARAM_TO = "to"

function readQueryParam(key: string): string | null {
	if (typeof window === "undefined") return null
	return new URLSearchParams(window.location.search).get(key)
}

function defaultFromDate(): string {
	const d = new Date()
	d.setDate(d.getDate() - 3)
	return toLocalInput(d)
}

export default function ChainShow({ chain }: PagePropsFor<"chain/show">) {
	const chainStr = chain as string
	const displayName = CHAIN_LABELS[chainStr] ?? chainStr

	const isCanton = chainStr === "CANTON"

	const finalityConfig = FINALITY_CONFIG[chainStr]
	const throughputConfig = THROUGHPUT_CHAINS.has(chainStr) ? makeFilteredConfig("/metrics/throughput", isCanton ? CANTON_THROUGHPUT_GROUPS : THROUGHPUT_GROUPS) : undefined
	const timeToFinalityConfig = TIME_TO_FINALITY_CHAINS.has(chainStr) ? makeFilteredConfig("/metrics/time-to-finality", TIME_TO_FINALITY_GROUPS) : undefined
	const l2TimingConfig = L2_TIMING_CONFIG[chainStr]
	const hasTimeToFinalityTab = Boolean(timeToFinalityConfig) || Boolean(l2TimingConfig)
	// Canton uses a dedicated governance-Nakamoto endpoint (unfiltered, single
	// synchronizer); the other chains share the stake-based decentralization view.
	const decentralizationConfig = isCanton
		? { endpoint: CANTON_DECENTRALIZATION_ENDPOINT, groups: CANTON_DECENTRALIZATION_GROUPS }
		: DECENTRALIZATION_CHAINS.has(chainStr)
			? makeFilteredConfig("/metrics/decentralization", DECENTRALIZATION_GROUPS)
			: undefined
	const decentralizationFavoriteMetric = isCanton ? "CANTON_GOV_NAKAMOTO_SAFETY" : "NAKAMOTO_SAFETY_COEFFICIENT"
	const governanceEventTypes = GOVERNANCE_EVENT_TYPES[chainStr]

	// Manual metrics fetch – determines which category tabs to show
	const [mmItems, setMmItems] = useState<MmItem[]>([])
	const [mmLoading, setMmLoading] = useState(true)
	useEffect(() => {
		const controller = new AbortController()
		setMmLoading(true)
		fetch(`/api/manual-metrics?chain=${chainStr}&pageSize=200`, { signal: controller.signal })
			.then((r) => (r.ok ? r.json() : Promise.resolve({ items: [] })))
			.then((data: { items?: MmItem[] }) => setMmItems(data.items ?? []))
			.catch(() => {})
			.finally(() => {
				if (!controller.signal.aborted) setMmLoading(false)
			})
		return () => controller.abort()
	}, [chainStr])

	const mmByCategory = useMemo(() => {
		const map: Record<string, MmItem[]> = {}
		for (const m of mmItems) {
			const list = map[m.category] ?? []
			list.push(m)
			map[m.category] = list
		}
		return map
	}, [mmItems])

	// Manual GOVERNANCE items render inside the unified Governance tab rather
	// than as their own category tab, so they're handled separately from the
	// other manual categories.
	const governanceManualItems = useMemo(() => mmByCategory[MM_GOVERNANCE_CATEGORY] ?? [], [mmByCategory])
	const hasGovernanceContent = Boolean(governanceEventTypes) || governanceManualItems.length > 0 || mmLoading

	// Categories with data, excluding GOVERNANCE (which lives in the unified tab).
	const mmCategories = useMemo(
		() => (mmLoading ? [] : MM_CATEGORY_ORDER.filter((cat) => cat !== MM_GOVERNANCE_CATEGORY && (mmByCategory[cat]?.length ?? 0) > 0)),
		[mmByCategory, mmLoading],
	)

	// Which tabs are available for this chain, in tab-order. Used to resolve the
	// "default tab" fallback and to reject unknown ``?tab=`` values from the URL.
	const availableTabs = useMemo(
		() =>
			[
				finalityConfig && "finality",
				throughputConfig && "throughput",
				hasTimeToFinalityTab && "time-to-finality",
				decentralizationConfig && "decentralization",
				hasGovernanceContent && "governance",
				...MM_CATEGORY_ORDER.filter((cat) => cat !== MM_GOVERNANCE_CATEGORY),
			].filter((t): t is string => Boolean(t)),
		[finalityConfig, throughputConfig, hasTimeToFinalityTab, decentralizationConfig, hasGovernanceContent],
	)
	const defaultTab = availableTabs[0] ?? "manual-metrics"

	// Initialise tab + date range from the URL so a shared link reproduces the
	// exact view. ``readQueryParam`` is SSR-safe (returns null on the server).
	const [tab, setTab] = useState<string>(() => {
		const fromUrl = readQueryParam(URL_PARAM_TAB)
		return fromUrl && availableTabs.includes(fromUrl) ? fromUrl : defaultTab
	})
	const [fromDate, setFromDate] = useState<string>(() => readQueryParam(URL_PARAM_FROM) ?? defaultFromDate())
	const [toDate, setToDate] = useState<string>(() => readQueryParam(URL_PARAM_TO) ?? "")

	// If the chain's supported tab set changes (e.g. switching chains via a
	// nested route) and the current tab is no longer valid, reset to default.
	useEffect(() => {
		if (!availableTabs.includes(tab)) setTab(defaultTab)
	}, [availableTabs, tab, defaultTab])

	// Reflect tab + range in the URL on every change. ``replaceState`` keeps the
	// browser back-stack clean so quick toggles don't flood history.
	useEffect(() => {
		if (typeof window === "undefined") return
		const params = new URLSearchParams(window.location.search)
		params.set(URL_PARAM_TAB, tab)
		if (fromDate) params.set(URL_PARAM_FROM, fromDate)
		else params.delete(URL_PARAM_FROM)
		if (toDate) params.set(URL_PARAM_TO, toDate)
		else params.delete(URL_PARAM_TO)
		const query = params.toString()
		const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`
		window.history.replaceState(window.history.state, "", nextUrl)
	}, [tab, fromDate, toDate])

	const handleRangeChange = useCallback((from: string, to: string) => {
		setFromDate(from)
		setToDate(to)
	}, [])

	return (
		<>
			<Head title={displayName} />
			<Header title={displayName} icon={<AssetLogo src={CHAIN_LOGOS[chainStr]} name={displayName} size={32} />} />
			<Container>
				<Tabs value={tab} onValueChange={setTab}>
					<TabsList>
						{finalityConfig && (
							<TabsTrigger value="finality" className={ACTIVE_TAB_CLASS}>
								Finality
							</TabsTrigger>
						)}
						{throughputConfig && (
							<TabsTrigger value="throughput" className={ACTIVE_TAB_CLASS}>
								Throughput
							</TabsTrigger>
						)}
						{hasTimeToFinalityTab && (
							<TabsTrigger value="time-to-finality" className={ACTIVE_TAB_CLASS}>
								Time to Finality
							</TabsTrigger>
						)}
						{decentralizationConfig && (
							<TabsTrigger value="decentralization" className={ACTIVE_TAB_CLASS}>
								Decentralization
							</TabsTrigger>
						)}
						{hasGovernanceContent && (
							<TabsTrigger value="governance" className={ACTIVE_TAB_CLASS}>
								Governance
							</TabsTrigger>
						)}
						{mmCategories.map((cat) => (
							<TabsTrigger key={cat} value={cat} className={ACTIVE_TAB_CLASS}>
								{MM_CATEGORY_LABELS[cat]}
							</TabsTrigger>
						))}
					</TabsList>
					{finalityConfig && (
						<TabsContent value="finality" className="mt-6">
							<MetricsTab
								chain={chainStr}
								config={finalityConfig}
								emptyMessage="No finality data available for this chain yet."
								errorPrefix="Failed to load finality data"
								fromDate={fromDate}
								toDate={toDate}
								onRangeChange={handleRangeChange}
								title="Finality"
								favoriteMetricType={CHAIN_FINALITY_METRIC[chainStr]}
								chartTooltip={
									FINALITY_EXPLANATIONS[chainStr] && FINALITY_CHART_KEYS[chainStr] ? { keys: FINALITY_CHART_KEYS[chainStr], text: FINALITY_EXPLANATIONS[chainStr] } : undefined
								}
							/>
						</TabsContent>
					)}
					{throughputConfig && (
						<TabsContent value="throughput" className="mt-6">
							<MetricsTab
								chain={chainStr}
								config={throughputConfig}
								emptyMessage="No throughput data available for this chain."
								errorPrefix="Failed to load throughput data"
								fromDate={fromDate}
								toDate={toDate}
								onRangeChange={handleRangeChange}
								title="Throughput"
								favoriteMetricType="TRANSACTIONS_PER_SECOND"
							/>
						</TabsContent>
					)}
					{hasTimeToFinalityTab && (
						<TabsContent value="time-to-finality" className="mt-6 space-y-8">
							{timeToFinalityConfig && (
								<MetricsTab
									chain={chainStr}
									config={timeToFinalityConfig}
									emptyMessage="No time-to-finality data available for this chain."
									errorPrefix="Failed to load time-to-finality data"
									fromDate={fromDate}
									toDate={toDate}
									onRangeChange={handleRangeChange}
								/>
							)}
							{l2TimingConfig && (
								<MetricsTab
									chain={chainStr}
									config={l2TimingConfig}
									emptyMessage="No L2 timing data available for this chain."
									errorPrefix="Failed to load L2 timing data"
									fromDate={fromDate}
									toDate={toDate}
									onRangeChange={handleRangeChange}
								/>
							)}
						</TabsContent>
					)}
					{decentralizationConfig && (
						<TabsContent value="decentralization" className="mt-6 space-y-8">
							<MetricsTab
								chain={chainStr}
								config={decentralizationConfig}
								emptyMessage="No decentralization data available for this chain."
								errorPrefix="Failed to load decentralization data"
								fromDate={fromDate}
								toDate={toDate}
								onRangeChange={handleRangeChange}
								title="Decentralization"
								favoriteMetricType={decentralizationFavoriteMetric}
							/>
							{OPERATOR_PANEL_CHAINS.has(chainStr) && <OperatorPanel chain={chainStr} />}
						</TabsContent>
					)}
					{hasGovernanceContent && (
						<TabsContent value="governance" className="mt-6">
							<GovernanceTab
								chain={chainStr}
								eventTypes={governanceEventTypes}
								manualItems={governanceManualItems}
								manualLoading={mmLoading}
								fromDate={fromDate}
								toDate={toDate}
								onRangeChange={handleRangeChange}
							/>
						</TabsContent>
					)}
					{MM_CATEGORY_ORDER.filter((cat) => cat !== MM_GOVERNANCE_CATEGORY).map((cat) => (
						<TabsContent key={cat} value={cat} className="mt-6">
							<ManualMetricCategoryContent items={mmByCategory[cat] ?? []} loading={mmLoading} />
						</TabsContent>
					))}
				</Tabs>
			</Container>
		</>
	)
}

ChainShow.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
