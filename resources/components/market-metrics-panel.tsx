// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { ChevronDown } from "lucide-react"
import type { ReactNode } from "react"
import { useState } from "react"
import { EvidenceTree } from "@/components/evidence-tree"
import { ScoreTrendChart } from "@/components/score-trend-chart"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Separator } from "@/components/ui/separator"
import { formatEvidenceKey, formatFinalPd, formatTerm } from "@/lib/format-evidence"

// ---------------------------------------------------------------------------
// Wire-shape types
// ---------------------------------------------------------------------------

interface PdBreakdown {
	finalPd: number | string
	anchorsTerm: number | string
	controlTerm: number | string
	assuranceTerm: number | string
	computedAt: string
	/**
	 * Per-metric contributions keyed by `anchors` / `controlModifiers` /
	 * `assurance`, each an array of `{ subCategory, weight, ... }`. Carries
	 * the weight actually applied to each sub-category, so the UI can badge
	 * the ones a weighting profile overrode.
	 */
	breakdown?: Record<string, unknown>
}

/** The weighting profile that shaped the displayed PD (null = default 1.0 everywhere). */
interface AppliedWeightingProfile {
	id: string
	name: string
	scope: "MARKET" | "PROTOCOL"
	isGlobal: boolean
	overrideCount: number
	teamName: string | null
	targetLabel: string | null
	targetProtocol: string | null
}

/** One anchor sub-category's judgment (scorer or manual). */
interface AnchorScoreRow {
	subCategory: string
	score: number | null
	pd: number | null
	conclusion: string | null
	rationale: string[]
	/** "scorer" (LLM output) or "manual" (operator-entered ANCHORS metric). */
	source?: string
}

/** One control sub-category's raw scorer judgment (score.controls[key]). */
interface ControlScoreRow {
	subCategory: string
	multiplier: number | null
	conclusion: string | null
	rationale: string[]
}

interface MarketScoring {
	anchors: AnchorScoreRow[]
	controls: ControlScoreRow[]
}

interface ScoreTrendPoint {
	capturedAt: string
	finalPd: number | string
	anchorsTerm: number | string
	controlTerm: number | string
	assuranceTerm: number | string
}

interface AssuranceItem {
	id: string
	name: string
	subCategory: string | null
	value: string | null
	riskScore: number | null
	notes: string | null
}

export interface MarketMetricsPanelProps {
	pd: PdBreakdown | null
	trend: ScoreTrendPoint[]
	/** Latest scorer per-sub-category output; null until the first score. */
	scoring: MarketScoring | null
	/** Top-level `anchors` tree from the latest COLLECT snapshot. */
	anchors: Record<string, unknown>
	/** Top-level `modifiers` tree from the latest COLLECT snapshot. */
	modifiers: Record<string, unknown>
	metricsCapturedAt: string | null
	assuranceMetrics: AssuranceItem[]
	/** The weighting profile that shaped the PD, or null when defaults applied. */
	appliedProfile?: AppliedWeightingProfile | null
	/** Optional slot for the favorite star; rendered next to the PD value. */
	favoriteSlot?: ReactNode
}

// ---------------------------------------------------------------------------
// Weight overrides (from pd.breakdown)
// ---------------------------------------------------------------------------

/** Build a `subCategory -> weight` map from one breakdown block. */
function weightMap(breakdown: Record<string, unknown> | undefined, blockKey: string): Map<string, number> {
	const out = new Map<string, number>()
	const block = breakdown?.[blockKey]
	if (!Array.isArray(block)) return out
	for (const item of block) {
		if (item && typeof item === "object") {
			const sub = (item as { subCategory?: unknown }).subCategory
			const w = (item as { weight?: unknown }).weight
			if (typeof sub === "string" && typeof w === "number") out.set(sub, w)
		}
	}
	return out
}

/** Trim trailing zeros so 1.5000 renders as "1.5", 2.0000 as "2". */
function formatWeight(w: number): string {
	return `${Number.parseFloat(w.toFixed(4))}`
}

/** Badge shown next to a sub-category whose applied weight differs from the default 1.0. */
function WeightBadge({ weights, subCategory }: { weights: Map<string, number>; subCategory: string }) {
	const w = weights.get(subCategory)
	if (w === undefined || w === 1) return null
	return (
		<Badge variant="outline" className="ml-2 font-mono tabular-nums" title="Weight applied by the active weighting profile">
			×{formatWeight(w)}
		</Badge>
	)
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

/**
 * Assembles every market-page panel.
 *
 *   <PdSummaryCard />   ← final PD + per-sub-category scoring tables
 *   <ScoreTrendChart />
 *   <AnchorsCard />      ← collector `anchors` tree
 *   <ModifiersCard />    ← collector `modifiers` tree
 *   <AssuranceCard />    ← manual ASSURANCE rows for the protocol
 *
 * Order matches the PRD's display flow. Every section degrades to an
 * empty state when its data is missing — a brand-new market with no
 * ticks yet shows informational placeholders rather than blank cards.
 */
export function MarketMetricsPanel({ pd, trend, scoring, anchors, modifiers, metricsCapturedAt, assuranceMetrics, appliedProfile, favoriteSlot }: MarketMetricsPanelProps) {
	const anchorWeights = weightMap(pd?.breakdown, "anchors")
	const controlWeights = weightMap(pd?.breakdown, "controlModifiers")
	const assuranceWeights = weightMap(pd?.breakdown, "assurance")
	return (
		<div className="space-y-6">
			<PdSummaryCard pd={pd} scoring={scoring} appliedProfile={appliedProfile} anchorWeights={anchorWeights} controlWeights={controlWeights} favoriteSlot={favoriteSlot} />
			<ScoreTrendChart points={trend} />
			<AnchorsCard anchors={anchors} capturedAt={metricsCapturedAt} />
			<ModifiersCard modifiers={modifiers} capturedAt={metricsCapturedAt} />
			<AssuranceCard items={assuranceMetrics} weights={assuranceWeights} />
		</div>
	)
}

/**
 * One-line summary of the weighting profile in effect, shown on the PD card.
 * Renders a muted "default weights" note when no profile matched.
 */
function AppliedProfileLine({ profile }: { profile?: AppliedWeightingProfile | null }) {
	if (!profile) {
		return <p className="text-xs text-muted-foreground">Default weights applied — no weighting profile matches this market.</p>
	}
	const owner = profile.isGlobal ? "Global default" : (profile.teamName ?? "Team")
	const scopeLabel = profile.scope === "MARKET" ? "this market" : `protocol ${profile.targetProtocol ?? ""}`.trim()
	const overrides = profile.overrideCount === 1 ? "1 weight override" : `${profile.overrideCount} weight overrides`
	return (
		<div className="flex flex-wrap items-center gap-2 text-xs">
			<span className="text-muted-foreground">Weighting profile:</span>
			<Badge variant="secondary">{profile.name}</Badge>
			<span className="text-muted-foreground">
				{owner} · scoped to {scopeLabel} · {overrides}
			</span>
		</div>
	)
}

// ---------------------------------------------------------------------------
// PD summary
// ---------------------------------------------------------------------------

function PdSummaryCard({
	pd,
	scoring,
	appliedProfile,
	anchorWeights,
	controlWeights,
	favoriteSlot,
}: {
	pd: PdBreakdown | null
	scoring: MarketScoring | null
	appliedProfile?: AppliedWeightingProfile | null
	anchorWeights: Map<string, number>
	controlWeights: Map<string, number>
	favoriteSlot?: ReactNode
}) {
	const hasScoring = scoring !== null && (scoring.anchors.length > 0 || scoring.controls.length > 0)
	return (
		<Card>
			<CardHeader>
				<div className="flex items-start justify-between gap-4">
					<div>
						<CardTitle>Probability of Default</CardTitle>
						<CardDescription>{pd?.computedAt ? `Last computed ${new Date(pd.computedAt).toLocaleString()}` : "Waiting for the first scorer tick."}</CardDescription>
					</div>
					{favoriteSlot}
				</div>
			</CardHeader>
			<CardContent>
				{pd === null && !hasScoring ? (
					<p className="text-sm text-muted-foreground">No PD has been computed yet. The scorer runs hourly; check back shortly after the next tick.</p>
				) : (
					<div className="space-y-6">
						{pd !== null && (
							<div className="flex flex-col gap-2 md:flex-row md:items-end md:gap-6">
								<span className="text-5xl font-semibold tabular-nums text-foreground">{formatFinalPd(pd.finalPd)}</span>
								<span className="text-sm text-muted-foreground">
									anchors {formatTerm(pd.anchorsTerm)} × control {formatTerm(pd.controlTerm)} × assurance {formatTerm(pd.assuranceTerm)}
								</span>
							</div>
						)}
						<AppliedProfileLine profile={appliedProfile} />
						{hasScoring && scoring !== null && (
							<>
								<Separator />
								<AnchorScoringSection rows={scoring.anchors} weights={anchorWeights} />
								<ControlScoringSection rows={scoring.controls} weights={controlWeights} />
							</>
						)}
					</div>
				)}
			</CardContent>
		</Card>
	)
}

/** Anchors scoring table — name · score · pd · conclusion, plus a rationale toggle. */
function AnchorScoringSection({ rows, weights }: { rows: AnchorScoreRow[]; weights: Map<string, number> }) {
	if (rows.length === 0) {
		return null
	}
	return (
		<div className="space-y-2">
			<h4 className="text-sm font-medium text-foreground">Anchors</h4>
			<table className="w-full text-sm text-foreground/90">
				<thead className="text-foreground/60">
					<tr>
						<th className="text-left font-medium pb-1">Name</th>
						<th className="text-right font-medium pb-1">Score</th>
						<th className="text-right font-medium pb-1">PD</th>
						<th className="text-left font-medium pb-1 pl-4">Conclusion</th>
					</tr>
				</thead>
				<tbody>
					{rows.map((r) => (
						<tr key={`${r.source ?? "scorer"}:${r.subCategory}`} className="border-t border-border/40 align-top">
							<td className="py-2 pr-2 font-medium">
								{formatEvidenceKey(r.subCategory)}
								{r.source === "manual" && (
									<Badge variant="secondary" className="ml-2 align-middle text-[10px]">
										Manual
									</Badge>
								)}
								<WeightBadge weights={weights} subCategory={r.subCategory} />
							</td>
							<td className="py-2 pr-2 text-right font-mono tabular-nums">{r.score == null ? "—" : r.score}</td>
							<td className="py-2 pr-2 text-right font-mono tabular-nums">{r.pd == null ? "—" : formatFinalPd(r.pd)}</td>
							<td className="py-2 pl-4 text-foreground/80">{r.conclusion ?? "—"}</td>
						</tr>
					))}
				</tbody>
			</table>
			<RationaleDisclosure rows={rows} />
		</div>
	)
}

/** Control scoring table — name · multiplier · conclusion, plus a rationale toggle. */
function ControlScoringSection({ rows, weights }: { rows: ControlScoreRow[]; weights: Map<string, number> }) {
	if (rows.length === 0) {
		return null
	}
	return (
		<div className="space-y-2">
			<h4 className="text-sm font-medium text-foreground">Controls</h4>
			<table className="w-full text-sm text-foreground/90">
				<thead className="text-foreground/60">
					<tr>
						<th className="text-left font-medium pb-1">Name</th>
						<th className="text-right font-medium pb-1">Multiplier</th>
						<th className="text-left font-medium pb-1 pl-4">Conclusion</th>
					</tr>
				</thead>
				<tbody>
					{rows.map((r) => (
						<tr key={r.subCategory} className="border-t border-border/40 align-top">
							<td className="py-2 pr-2 font-medium">
								{formatEvidenceKey(r.subCategory)}
								<WeightBadge weights={weights} subCategory={r.subCategory} />
							</td>
							<td className="py-2 pr-2 text-right font-mono tabular-nums">{r.multiplier == null ? "—" : formatTerm(r.multiplier)}</td>
							<td className="py-2 pl-4 text-foreground/80">{r.conclusion ?? "—"}</td>
						</tr>
					))}
				</tbody>
			</table>
			<RationaleDisclosure rows={rows} />
		</div>
	)
}

/**
 * "Show rationale" hyperlink under a scoring table.
 *
 * Collapsed by default; expands to each sub-category's rationale array
 * (one bullet per string). Sub-categories with no rationale are skipped;
 * the toggle hides entirely when nothing has a rationale.
 */
function RationaleDisclosure({ rows }: { rows: { subCategory: string; rationale: string[] }[] }) {
	const [open, setOpen] = useState(false)
	const withRationale = rows.filter((r) => r.rationale.length > 0)
	if (withRationale.length === 0) {
		return null
	}
	return (
		<Collapsible open={open} onOpenChange={setOpen}>
			<CollapsibleTrigger className="group inline-flex items-center gap-1 text-sm text-primary underline-offset-4 hover:underline cursor-pointer">
				{open ? "Hide rationale" : "Show rationale"}
				<ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
			</CollapsibleTrigger>
			<CollapsibleContent className="mt-2 space-y-3 border-l border-border pl-4">
				{withRationale.map((r) => (
					<div key={r.subCategory} className="space-y-1">
						<p className="text-sm font-medium text-foreground">{formatEvidenceKey(r.subCategory)}</p>
						<ul className="list-disc pl-5 space-y-1 text-sm text-foreground/80">
							{r.rationale.map((line) => (
								<li key={`${r.subCategory}-${line}`}>{line}</li>
							))}
						</ul>
					</div>
				))}
			</CollapsibleContent>
		</Collapsible>
	)
}

// ---------------------------------------------------------------------------
// Anchors / Modifiers
// ---------------------------------------------------------------------------

/**
 * Anchors card — the collector's `anchors` tree.
 *
 * The tree is a category → metric-dict map (e.g.
 * `marketSolvency.totalSupplied`); {@link EvidenceTree} renders each
 * top-level category as a collapsible block and recurses into nested
 * objects/arrays, so deeply-nested values expand on demand.
 */
function AnchorsCard({ anchors, capturedAt }: { anchors: Record<string, unknown>; capturedAt: string | null }) {
	return (
		<Card>
			<CardHeader>
				<CardTitle>Anchors</CardTitle>
				<CardDescription>{capturedAt ? `Captured ${new Date(capturedAt).toLocaleString()}` : "No collector run yet."}</CardDescription>
			</CardHeader>
			<CardContent>
				<EvidenceTree evidence={anchors} emptyMessage="No collector run has populated anchors yet." />
			</CardContent>
		</Card>
	)
}

/**
 * Modifiers card — the collector's `modifiers` tree. Same category →
 * metric-dict shape and same collapsible recursion as the anchors card.
 */
function ModifiersCard({ modifiers, capturedAt }: { modifiers: Record<string, unknown>; capturedAt: string | null }) {
	return (
		<Card>
			<CardHeader>
				<CardTitle>Modifiers</CardTitle>
				<CardDescription>{capturedAt ? `Captured ${new Date(capturedAt).toLocaleString()}` : "No collector run yet."}</CardDescription>
			</CardHeader>
			<CardContent>
				<EvidenceTree evidence={modifiers} emptyMessage="No collector run has populated modifiers yet." />
			</CardContent>
		</Card>
	)
}

// ---------------------------------------------------------------------------
// Assurance manual metrics
// ---------------------------------------------------------------------------

function AssuranceCard({ items, weights }: { items: AssuranceItem[]; weights: Map<string, number> }) {
	// Each protocol assurance metric ships as two rows sharing a name: an
	// "Evidence" row (qualitative, no value) and a "Multiplier" row (the
	// numeric multiplier). Collapse by name so each evidence category shows
	// on one line with its multiplier. The dimension `name` is also the key
	// a weighting profile targets, so look up its applied weight by name.
	const rows = new Map<string, { name: string; multiplier: number | null }>()
	for (const m of items) {
		const row = rows.get(m.name) ?? { name: m.name, multiplier: null }
		if (m.value != null && m.value !== "") {
			row.multiplier = Number(m.value)
		}
		rows.set(m.name, row)
	}
	const list = [...rows.values()]
	// Only surface the "Adjusted" column when some dimension carries a
	// non-default weight — otherwise it's just a duplicate of Multiplier.
	const anyWeighted = list.some((r) => {
		const w = weights.get(r.name)
		return w !== undefined && w !== 1
	})
	return (
		<Card>
			<CardHeader>
				<CardTitle>Assurance (Manual)</CardTitle>
				<CardDescription>Operator-published ASSURANCE multipliers for this protocol.</CardDescription>
			</CardHeader>
			<CardContent>
				{list.length === 0 ? (
					<p className="text-sm text-muted-foreground">No ASSURANCE metrics published for this protocol.</p>
				) : (
					<table className="w-full text-sm text-foreground/90">
						<thead className="text-foreground/60">
							<tr>
								<th className="text-left font-medium pb-1">Evidence</th>
								<th className="text-right font-medium pb-1">Multiplier</th>
								{anyWeighted && <th className="text-right font-medium pb-1">Adjusted</th>}
							</tr>
						</thead>
						<tbody>
							{list.map((r) => {
								const w = weights.get(r.name)
								const adjusted = r.multiplier != null && w !== undefined ? r.multiplier * w : r.multiplier
								return (
									<tr key={r.name} className="border-t border-border/40">
										<td className="py-2 pr-2 font-medium">
											{r.name}
											<WeightBadge weights={weights} subCategory={r.name} />
										</td>
										<td className="py-2 pl-4 text-right font-mono tabular-nums">{r.multiplier == null ? "—" : `×${r.multiplier.toFixed(3)}`}</td>
										{anyWeighted && <td className="py-2 pl-4 text-right font-mono tabular-nums">{adjusted == null ? "—" : `×${adjusted.toFixed(3)}`}</td>}
									</tr>
								)
							})}
						</tbody>
					</table>
				)}
			</CardContent>
		</Card>
	)
}
