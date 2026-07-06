// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { useState } from "react"
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"

interface ScoreTrendPoint {
	capturedAt: string
	finalPd: number | string
	anchorsTerm: number | string
	controlTerm: number | string
	assuranceTerm: number | string
}

interface ScoreTrendChartProps {
	/**
	 * Time series of computed PDs, oldest first. The component renders
	 * up to whatever the server sent — the controller caps at 168 hourly
	 * points (one week).
	 */
	points: ScoreTrendPoint[]
}

type SeriesKey = "finalPd" | "anchorsTerm" | "controlTerm" | "assuranceTerm"

const CHART_CONFIG = {
	finalPd: {
		label: "Probability of Default",
		color: "var(--color-chart-1)",
	},
	anchorsTerm: {
		label: "Anchors term",
		color: "var(--color-chart-2)",
	},
	controlTerm: {
		label: "Control term",
		color: "var(--color-chart-3)",
	},
	assuranceTerm: {
		label: "Assurance term",
		color: "var(--color-chart-4)",
	},
} satisfies ChartConfig

const TOGGLEABLE_TERMS: { key: Exclude<SeriesKey, "finalPd">; label: string }[] = [
	{ key: "anchorsTerm", label: "Anchors" },
	{ key: "controlTerm", label: "Control" },
	{ key: "assuranceTerm", label: "Assurance" },
]

function toNumber(value: number | string): number {
	return typeof value === "number" ? value : Number(value)
}

function formatTick(value: string): string {
	const date = new Date(value)
	if (Number.isNaN(date.getTime())) return value
	return date.toLocaleString(undefined, {
		month: "short",
		day: "numeric",
		hour: "numeric",
	})
}

/**
 * Render a time series of a market's PD breakdown.
 *
 * The "Probability of Default" line is always shown; the per-term
 * overlays toggle on demand so the chart stays readable when the
 * three modifier lines move in opposite directions. Hidden when fewer
 * than two points are available — a single point isn't a trend.
 */
export function ScoreTrendChart({ points }: ScoreTrendChartProps) {
	const [activeTerms, setActiveTerms] = useState<Set<SeriesKey>>(new Set(["finalPd"]))

	const toggle = (key: Exclude<SeriesKey, "finalPd">) => {
		setActiveTerms((prev) => {
			const next = new Set(prev)
			if (next.has(key)) next.delete(key)
			else next.add(key)
			return next
		})
	}

	if (points.length < 2) {
		return (
			<Card>
				<CardHeader>
					<CardTitle>PD trend</CardTitle>
					<CardDescription>Need at least two scorer ticks before a trend can be drawn. Check back in about an hour.</CardDescription>
				</CardHeader>
			</Card>
		)
	}

	const data = points.map((p) => ({
		capturedAt: p.capturedAt,
		finalPd: toNumber(p.finalPd),
		anchorsTerm: toNumber(p.anchorsTerm),
		controlTerm: toNumber(p.controlTerm),
		assuranceTerm: toNumber(p.assuranceTerm),
	}))

	return (
		<Card>
			<CardHeader>
				<div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
					<div>
						<CardTitle>PD trend</CardTitle>
						<CardDescription>{points.length} hourly snapshots</CardDescription>
					</div>
					<div className="flex flex-wrap items-center gap-2">
						{TOGGLEABLE_TERMS.map(({ key, label }) => (
							<Button key={key} size="sm" variant={activeTerms.has(key) ? "default" : "outline"} onClick={() => toggle(key)}>
								{label}
							</Button>
						))}
					</div>
				</div>
			</CardHeader>
			<CardContent>
				<ChartContainer config={CHART_CONFIG} className="h-[300px] w-full">
					<LineChart data={data} margin={{ top: 8, right: 24, left: 8, bottom: 8 }}>
						<CartesianGrid vertical={false} />
						<XAxis dataKey="capturedAt" tickFormatter={formatTick} minTickGap={48} />
						<YAxis tickFormatter={(value: number) => value.toFixed(2)} width={48} />
						<ChartTooltip content={<ChartTooltipContent labelFormatter={formatTick} />} />
						<Line type="monotone" dataKey="finalPd" stroke="var(--color-finalPd)" strokeWidth={2} dot={false} name="Probability of Default" />
						{activeTerms.has("anchorsTerm") && (
							<Line type="monotone" dataKey="anchorsTerm" stroke="var(--color-anchorsTerm)" strokeWidth={1.5} dot={false} strokeDasharray="4 2" name="Anchors term" />
						)}
						{activeTerms.has("controlTerm") && (
							<Line type="monotone" dataKey="controlTerm" stroke="var(--color-controlTerm)" strokeWidth={1.5} dot={false} strokeDasharray="4 2" name="Control term" />
						)}
						{activeTerms.has("assuranceTerm") && (
							<Line type="monotone" dataKey="assuranceTerm" stroke="var(--color-assuranceTerm)" strokeWidth={1.5} dot={false} strokeDasharray="4 2" name="Assurance term" />
						)}
					</LineChart>
				</ChartContainer>
			</CardContent>
		</Card>
	)
}
