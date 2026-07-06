// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { ChevronDown } from "lucide-react"
import type React from "react"
import { useEffect, useMemo, useState } from "react"
import { FavoriteButton } from "@/components/favorite-button"

interface MetricItem {
	id: string
	name: string
	desc: string
	category: string
	subCategory?: string | null
	value?: string | null
}

interface CSVTable2Col {
	headers: [string, string]
	rows: [string, string][]
}

interface CSVTableFull {
	headers: string[]
	rows: string[][]
}

interface MetricPair {
	name: string
	evidence: MetricItem | undefined
	scoring: MetricItem | undefined
}

interface Props {
	protocol?: string
	market?: string
	token?: string
	emptyMessage?: string
}

/**
 * Maps the entity filter onto the "summary" manual-metric category.
 *
 * Tokens carry a single ``TOKEN_SCORE`` summary row (where the computed
 * Probability of Default lives); protocols and markets carry a
 * ``PROTOCOL_SCORE`` summary. Same shape (``sub_category === "SUMMARY"``,
 * percentage in ``value``), different category enum. Both are favoritable.
 * Defaults to ``PROTOCOL_SCORE`` so callers that pass neither still
 * render protocol-style.
 */
function summaryCategoryFor(token: string | undefined): "PROTOCOL_SCORE" | "TOKEN_SCORE" {
	return token ? "TOKEN_SCORE" : "PROTOCOL_SCORE"
}

function parseRow2Col(line: string): [string, string] {
	const idx = line.indexOf(",")
	if (idx === -1) return [line.trim(), ""]
	return [line.slice(0, idx).trim(), line.slice(idx + 1).trim()]
}

function parseCSV2Col(text: string | null | undefined): CSVTable2Col {
	if (!text?.trim()) return { headers: ["", ""], rows: [] }
	const lines = text.trim().split("\n")
	const headers = parseRow2Col(lines[0])
	const rows = lines.slice(1).map(parseRow2Col)
	return { headers, rows }
}

function parseBullets(text: string | null | undefined): string[] {
	if (!text?.trim()) return []
	return text
		.trim()
		.split("\n")
		.map((s) => s.trim())
		.filter(Boolean)
}

function pairMetrics(metrics: MetricItem[], evidenceSub: string, scoringSub: string): { pairs: MetricPair[]; otherUnpaired: MetricItem[] } {
	const evidenceMap = new Map<string, MetricItem>()
	const scoringMap = new Map<string, MetricItem>()
	const order: string[] = []
	const seen = new Set<string>()
	const otherUnpaired: MetricItem[] = []

	for (const m of metrics) {
		if (m.subCategory === evidenceSub) {
			if (!seen.has(m.name)) {
				order.push(m.name)
				seen.add(m.name)
			}
			evidenceMap.set(m.name, m)
		} else if (m.subCategory === scoringSub) {
			if (!seen.has(m.name)) {
				order.push(m.name)
				seen.add(m.name)
			}
			scoringMap.set(m.name, m)
		} else {
			// Anything outside the legacy Evidence/Scoring CSV shape goes
			// into the summary list with its own value (user-created
			// metrics whose sub-category is neither Evidence nor the
			// configured scoring keyword).
			otherUnpaired.push(m)
		}
	}

	// A pair may now have either an evidence half, a scoring half, or both.
	// Evidence-half-only pairs render with no value; scoring-half-only pairs
	// render in the summary table but not in the "Detailed explanation".
	return {
		pairs: order.map((name) => ({
			name,
			evidence: evidenceMap.get(name),
			scoring: scoringMap.get(name),
		})),
		otherUnpaired,
	}
}

function SummaryTable({ table }: { table: CSVTableFull }) {
	if (!table.rows.length) return null
	return (
		<div className="rounded-md border border-border overflow-hidden">
			<table className="w-full text-sm">
				<thead>
					<tr className="bg-muted">
						{table.headers.map((h) => (
							<th key={h} className="text-left px-4 py-2.5 font-semibold text-foreground">
								{h}
							</th>
						))}
					</tr>
				</thead>
				<tbody>
					{table.rows.map((row, i) => (
						<tr key={i} className="border-t border-border even:bg-muted/30">
							{row.map((cell, j) => (
								<td key={j} className="px-4 py-2.5 text-foreground">
									{cell}
								</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
		</div>
	)
}

function EvidenceTable({ table }: { table: CSVTable2Col }) {
	if (!table.rows.length) return null
	const allRows: [string, string][] = [table.headers, ...table.rows]
	return (
		<div className="rounded-md border border-border overflow-hidden">
			<table className="w-full text-sm">
				<tbody>
					{allRows.map((row, i) => (
						<tr key={i} className={i > 0 ? "border-t border-border even:bg-muted/30" : ""}>
							{row.map((cell, j) => (
								<td key={j} className="px-3 py-2 text-xs text-foreground">
									{cell}
								</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
		</div>
	)
}

function Expandable({ open, children }: { open: boolean; children: React.ReactNode }) {
	return (
		<div
			style={{
				display: "grid",
				gridTemplateRows: open ? "1fr" : "0fr",
				transition: "grid-template-rows 300ms ease",
			}}
		>
			<div className="overflow-hidden">{children}</div>
		</div>
	)
}

function MetricDetailCard({ pair }: { pair: MetricPair }) {
	const evidenceTable = parseCSV2Col(pair.evidence?.desc)
	const scoringBullets = parseBullets(pair.scoring?.desc)
	const scoreValue = pair.scoring?.value

	return (
		<div className="rounded-lg border border-border p-5">
			<h4 className="font-semibold text-base mb-4">{pair.name}</h4>
			<div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
				<div>
					<p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">Evidence</p>
					<EvidenceTable table={evidenceTable} />
				</div>
				<div>
					<div className="flex items-baseline gap-3 mb-3">
						<p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Score</p>
						{scoreValue && <span className="text-3xl font-bold text-foreground">{scoreValue}</span>}
					</div>
					{scoringBullets.length > 0 && (
						<ul className="space-y-2">
							{scoringBullets.map((bullet, i) => (
								<li key={i} className="flex gap-2 text-sm text-muted-foreground">
									<span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/50" />
									<span>{bullet}</span>
								</li>
							))}
						</ul>
					)}
				</div>
			</div>
		</div>
	)
}

function buildSummaryTable(pairs: MetricPair[], otherUnpaired: MetricItem[]): CSVTableFull {
	const rows: string[][] = []
	for (const p of pairs) {
		rows.push([p.name, p.scoring?.value ?? ""])
	}
	for (const m of otherUnpaired) {
		rows.push([m.name, m.value ?? ""])
	}
	return { headers: ["Name", "Value"], rows }
}

function CategorySection({ title, pairs, otherUnpaired }: { title: string; pairs: MetricPair[]; otherUnpaired: MetricItem[] }) {
	const [open, setOpen] = useState(false)
	const summaryTable = buildSummaryTable(pairs, otherUnpaired)
	// Only pairs with an evidence half belong in the "Detailed explanation"
	// expander — scoring-only entries appear in the summary table above and
	// have no extra detail to show.
	const detailedPairs = pairs.filter((p) => p.evidence)

	if (summaryTable.rows.length === 0) return null

	return (
		<section className="space-y-4">
			<h2 className="text-xl font-bold tracking-tight">{title}</h2>
			<SummaryTable table={summaryTable} />
			{detailedPairs.length > 0 && (
				<>
					<button type="button" onClick={() => setOpen((o) => !o)} className="flex items-center gap-1.5 text-sm font-medium text-primary hover:underline">
						Detailed explanation
						<ChevronDown className="h-4 w-4 transition-transform duration-200" style={{ transform: open ? "rotate(180deg)" : "rotate(0deg)" }} />
					</button>
					<Expandable open={open}>
						<div className="space-y-4 pt-2">
							{detailedPairs.map((pair) => (
								<MetricDetailCard key={pair.name} pair={pair} />
							))}
						</div>
					</Expandable>
				</>
			)}
		</section>
	)
}

export function ProtocolMetricsPanel({ protocol, market, token, emptyMessage }: Props) {
	const [items, setItems] = useState<MetricItem[]>([])
	const [loading, setLoading] = useState(true)
	const [error, setError] = useState<string | null>(null)
	const summaryCategory = summaryCategoryFor(token)
	const summaryTitle = token ? "Token risk" : "Protocol score"

	useEffect(() => {
		const params = new URLSearchParams({ pageSize: "200" })
		if (protocol) params.set("protocol", protocol)
		if (market) params.set("market", market)
		if (token) params.set("token", token)

		const controller = new AbortController()
		setLoading(true)
		setError(null)

		fetch(`/api/manual-metrics?${params}`, { signal: controller.signal })
			.then(async (res) => {
				if (!res.ok) throw new Error(`HTTP ${res.status}`)
				const data = (await res.json()) as { items?: MetricItem[] }
				return data.items ?? []
			})
			.then(setItems)
			.catch((err: Error) => {
				if (err.name !== "AbortError") setError(err.message)
			})
			.finally(() => {
				if (!controller.signal.aborted) setLoading(false)
			})

		return () => controller.abort()
	}, [protocol, market, token])

	const { summaryPD, summaryPDId, anchorPairs, anchorOther, controlPairs, controlOther, assurancePairs, assuranceOther, otherSummaryRows } = useMemo(() => {
		const summaryRows = items.filter((m) => m.category === summaryCategory)
		const anchorsMetrics = items.filter((m) => m.category === "ANCHORS")
		const controlMetrics = items.filter((m) => m.category === "CONTROL")
		const assuranceMetrics = items.filter((m) => m.category === "ASSURANCE")
		const summaryRow = summaryRows.find((m) => m.subCategory === "SUMMARY")

		// Legacy seed format used these sub_category buckets to carry CSV
		// summary blobs. We no longer render those CSVs, but we also don't
		// want them showing up as rogue rows in the new per-category
		// summary tables.
		const legacyBuckets = new Set(["SUMMARY", "ANCHOR", "CONTROL", "ASSURANCE"])
		const otherSummary = summaryRows.filter((m) => !m.subCategory || !legacyBuckets.has(m.subCategory))

		const anchors = pairMetrics(anchorsMetrics, "Evidence", "Risk score")
		const controls = pairMetrics(controlMetrics, "Evidence", "Multiplier")
		const assurances = pairMetrics(assuranceMetrics, "Evidence", "Multiplier")

		return {
			summaryPD: summaryRow?.value ?? null,
			// Favorites accept shared, published PROTOCOL_SCORE and TOKEN_SCORE
			// summary rows at the API layer. Surface the favorite button
			// whenever we have one of those summary rows so the user never
			// sees a star that 4xxs.
			summaryPDId: summaryRow && (summaryCategory === "PROTOCOL_SCORE" || summaryCategory === "TOKEN_SCORE") ? summaryRow.id : null,
			anchorPairs: anchors.pairs,
			anchorOther: anchors.otherUnpaired,
			controlPairs: controls.pairs,
			controlOther: controls.otherUnpaired,
			assurancePairs: assurances.pairs,
			assuranceOther: assurances.otherUnpaired,
			otherSummaryRows: otherSummary,
		}
	}, [items, summaryCategory])

	if (loading) return <p className="text-muted-foreground text-sm">Loading metrics…</p>
	if (error) return <p className="text-destructive text-sm">Failed to load metrics: {error}</p>
	if (items.length === 0) return <p className="text-muted-foreground text-sm">{emptyMessage ?? "No metrics published yet."}</p>

	return (
		<div className="space-y-12">
			{summaryPD && (
				<div className="relative rounded-xl border border-border bg-card p-8 text-center shadow-sm">
					{summaryPDId && (
						<div className="absolute top-3 right-3">
							<FavoriteButton size="md" target={{ kind: "manual", manualMetricId: summaryPDId }} />
						</div>
					)}
					<p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground mb-3">Probability of Default</p>
					<p className="text-6xl font-bold tracking-tight text-foreground">{summaryPD}</p>
				</div>
			)}
			<CategorySection title="Anchors" pairs={anchorPairs} otherUnpaired={anchorOther} />
			<CategorySection title="Control" pairs={controlPairs} otherUnpaired={controlOther} />
			<CategorySection title="Assurance" pairs={assurancePairs} otherUnpaired={assuranceOther} />
			{otherSummaryRows.length > 0 && <CategorySection title={summaryTitle} pairs={[]} otherUnpaired={otherSummaryRows} />}
		</div>
	)
}
