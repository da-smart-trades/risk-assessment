// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head } from "@inertiajs/react"
import type React from "react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"
import { AssetLogo } from "@/components/asset-logo"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { ProtocolMetricsPanel } from "@/components/protocol-metrics-panel"
import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { TOKEN_LOGOS } from "@/lib/logos"
import { cn } from "@/lib/utils"

// Selected tab uses brand green text instead of the default gradient-fill pill.
const ACTIVE_TAB_CLASS = "data-[state=active]:bg-transparent data-[state=active]:text-primary data-[state=active]:shadow-none"

const TOKEN_LABELS: Record<string, string> = {
	WETH: "WETH",
	USDE: "USDe",
	AAVE: "Aave (AAVE)",
	UNI: "Uniswap (UNI)",
	USDC: "USDC",
	USDT0: "USDT0",
	AUSDC: "aUSDC",
	CUSDC: "cUSDC",
	LINK: "LINK",
	STETH: "stETH",
	WSTETH: "wstETH",
}

const CHAIN_LABELS: Record<string, string> = {
	ETHEREUM: "Ethereum",
	ARBITRUM: "Arbitrum",
	BASE: "Base",
	INK: "Ink",
	UNICHAIN: "Unichain",
	POLYGON: "Polygon",
	AVALANCHE_C: "Avalanche C",
	OPTIMISM: "Optimism",
	SOLANA: "Solana",
}

// Maps every metric_type value to a human-readable chart title
const METRIC_LABEL: Record<string, string> = {
	USDC_TOTAL_SUPPLY: "Total Supply",
	USDC_INFLOW: "Inflow",
	USDC_OUTFLOW: "Outflow",
	USDC_TRANSACTION_COUNT: "Transfer Count",
	USDC_UNIQUE_ADDRESSES: "Unique Addresses",
	USDT0_TOTAL_AMOUNT_TRANSFERS: "Volume",
	USDT0_INFLOW: "Inflow",
	USDT0_OUTFLOW: "Outflow",
	USDT0_TRANSACTION_COUNT: "Transfer Count",
	USDT0_UNIQUE_ADDRESSES: "Unique Addresses",
	ETH_WETH_TOTAL_SUPPLY: "Total Supply",
	ETH_WETH_INFLOW: "Inflow",
	ETH_WETH_OUTFLOW: "Outflow",
	ETH_USDE_TOTAL_SUPPLY: "Total Supply",
	ETH_USDE_VOLUME: "Volume",
	ETH_USDE_TRANSFER_COUNT: "Transfer Count",
	ETH_USDE_UNIQUE_ADDRESSES: "Unique Addresses",
	ETH_AAVE_TOTAL_SUPPLY: "Total Supply",
	ETH_AAVE_VOLUME: "Volume",
	ETH_AAVE_TRANSFER_COUNT: "Transfer Count",
	ETH_AAVE_UNIQUE_ADDRESSES: "Unique Addresses",
	ETH_UNI_TOTAL_SUPPLY: "Total Supply",
	ETH_UNI_VOLUME: "Volume",
	ETH_UNI_TRANSFER_COUNT: "Transfer Count",
	ETH_UNI_UNIQUE_ADDRESSES: "Unique Addresses",
	ETH_AUSDC_TOTAL_SUPPLY: "Total Supply",
	ETH_AUSDC_VOLUME: "Volume",
	ETH_AUSDC_TRANSFER_COUNT: "Transfer Count",
	ETH_AUSDC_UNIQUE_ADDRESSES: "Unique Addresses",
	ETH_CUSDC_TOTAL_SUPPLY: "Total Supply",
	ETH_CUSDC_VOLUME: "Volume",
	ETH_CUSDC_TRANSFER_COUNT: "Transfer Count",
	ETH_CUSDC_UNIQUE_ADDRESSES: "Unique Addresses",
	ETH_LINK_TOTAL_SUPPLY: "Total Supply",
	ETH_LINK_VOLUME: "Volume",
	ETH_LINK_TRANSFER_COUNT: "Transfer Count",
	ETH_LINK_UNIQUE_ADDRESSES: "Unique Addresses",
	ETH_STETH_TOTAL_SUPPLY: "Total Supply",
	ETH_STETH_VOLUME: "Volume",
	ETH_STETH_TRANSFER_COUNT: "Transfer Count",
	ETH_STETH_UNIQUE_ADDRESSES: "Unique Addresses",
	ETH_WSTETH_TOTAL_SUPPLY: "Total Supply",
	ETH_WSTETH_VOLUME: "Volume",
	ETH_WSTETH_TRANSFER_COUNT: "Transfer Count",
	ETH_WSTETH_UNIQUE_ADDRESSES: "Unique Addresses",
	ETH_CBBTC_TOTAL_SUPPLY: "Total Supply",
	ETH_CBBTC_VOLUME: "Volume",
	ETH_CBBTC_TRANSFER_COUNT: "Transfer Count",
	ETH_CBBTC_UNIQUE_ADDRESSES: "Unique Addresses",
}

// Display order within a chain section
const METRIC_ORDER = ["Total Supply", "Inflow", "Outflow", "Volume", "Transfer Count", "Unique Addresses"]

interface TokenActivityRow {
	id: string
	createdAt: string
	chain: string
	token: string
	metricType: string
	value: string
}

const INTERVALS: Record<string, number> = {
	"1h": 60 * 60 * 1000,
	"6h": 6 * 60 * 60 * 1000,
	"24h": 24 * 60 * 60 * 1000,
	"7d": 7 * 24 * 60 * 60 * 1000,
	"30d": 30 * 24 * 60 * 60 * 1000,
}

function toLocalInput(d: Date): string {
	return d.toISOString().slice(0, 16)
}

function defaultFromDate(): string {
	const d = new Date()
	d.setDate(d.getDate() - 3)
	return toLocalInput(d)
}

function IntervalSelector({ value, onChange }: { value: string; onChange: (v: string) => void }) {
	const options = ["1h", "6h", "24h", "7d", "30d", "all"] as const
	return (
		<div className="flex gap-1 justify-end mt-1">
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

const ZOOM_IN_FACTOR = 0.85
const ZOOM_OUT_FACTOR = 1 / ZOOM_IN_FACTOR
const MIN_VIEW_SIZE = 5

type ZoomView = { start: number; size: number } | null

function MetricChart({ title, rows }: { title: string; rows: TokenActivityRow[] }) {
	const [interval, setInterval] = useState("all")
	const [view, setView] = useState<ZoomView>(null)
	const wheelHandlerRef = useRef<((e: WheelEvent) => void) | null>(null)
	const containerElRef = useRef<HTMLDivElement | null>(null)

	const filtered = useMemo(() => {
		const sorted = [...rows].sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime())
		if (interval === "all") return sorted
		const ms = INTERVALS[interval]
		const cutoff = Date.now() - ms
		return sorted.filter((r) => {
			const t = new Date(r.createdAt).getTime()
			return !Number.isNaN(t) && t >= cutoff
		})
	}, [rows, interval])

	const chartConfig: ChartConfig = useMemo(() => ({ value: { label: title, color: "var(--color-chart-1)" } }), [title])

	const chartData = useMemo(
		() =>
			filtered.map((r) => ({
				time: new Date(r.createdAt).toLocaleString(),
				value: Number.parseFloat(r.value),
			})),
		[filtered],
	)

	// biome-ignore lint/correctness/useExhaustiveDependencies: interval change resets zoom
	useEffect(() => {
		setView(null)
	}, [interval])

	const visibleData = useMemo(() => {
		if (view === null) return chartData
		return chartData.slice(view.start, view.start + view.size)
	}, [chartData, view])

	useEffect(() => {
		wheelHandlerRef.current = (e: WheelEvent) => {
			const total = chartData.length
			if (total <= MIN_VIEW_SIZE) return
			e.preventDefault()
			const el = containerElRef.current
			if (!el) return
			const rect = el.getBoundingClientRect()
			if (rect.width <= 0) return
			const cursorFrac = Math.max(0, Math.min(rect.width, e.clientX - rect.left)) / rect.width
			setView((prev) => {
				const cur = prev ?? { start: 0, size: total }
				const factor = e.deltaY < 0 ? ZOOM_IN_FACTOR : ZOOM_OUT_FACTOR
				const newSize = Math.max(MIN_VIEW_SIZE, Math.min(total, Math.round(cur.size * factor)))
				if (newSize >= total) return null
				if (newSize === cur.size) return cur
				const rawStart = Math.round(cur.start + cursorFrac * cur.size - cursorFrac * newSize)
				return { start: Math.max(0, Math.min(total - newSize, rawStart)), size: newSize }
			})
		}
	}, [chartData.length])

	const containerRef = useCallback((el: HTMLDivElement | null) => {
		containerElRef.current = el
		if (!el) return
		const handler = (e: WheelEvent) => wheelHandlerRef.current?.(e)
		el.addEventListener("wheel", handler, { passive: false })
		return () => el.removeEventListener("wheel", handler)
	}, [])

	return (
		<div className="space-y-1">
			<div className="flex items-center justify-between">
				<p className="text-sm font-medium text-foreground">{title}</p>
				{view !== null && (
					<button type="button" onClick={() => setView(null)} className="text-xs text-muted-foreground hover:text-foreground transition-colors">
						↩ Reset zoom
					</button>
				)}
			</div>
			{chartData.length === 0 ? (
				<div className="flex h-44 items-center justify-center rounded-lg border border-border text-muted-foreground text-sm">No data for this interval</div>
			) : (
				<div ref={containerRef} role="img" aria-label={title} onDoubleClick={() => setView(null)} className="select-none cursor-crosshair">
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
							<Area type="monotone" dataKey="value" stroke="var(--color-chart-1)" fill="var(--color-chart-1)" fillOpacity={0.15} dot={false} connectNulls={false} />
						</AreaChart>
					</ChartContainer>
				</div>
			)}
			<IntervalSelector value={interval} onChange={setInterval} />
		</div>
	)
}

function TokenFlowTab({ token, fromDate, toDate, onRangeChange }: { token: string; fromDate: string; toDate: string; onRangeChange: (f: string, t: string) => void }) {
	const [rows, setRows] = useState<TokenActivityRow[]>([])
	const [loading, setLoading] = useState(true)
	const [error, setError] = useState<string | null>(null)
	const [tick, setTick] = useState(0)
	const prevKeyRef = useRef("")

	useEffect(() => {
		const id = setInterval(() => setTick((t) => t + 1), 5000)
		return () => clearInterval(id)
	}, [])

	// biome-ignore lint/correctness/useExhaustiveDependencies: tick drives periodic refresh
	useEffect(() => {
		const newKey = `${token}|${fromDate}|${toDate}`
		const paramChanged = prevKeyRef.current !== newKey
		prevKeyRef.current = newKey
		if (paramChanged) {
			setLoading(true)
			setError(null)
		}

		const controller = new AbortController()
		const params = new URLSearchParams({ token, pageSize: "2000" })
		if (fromDate) params.set("createdAfter", new Date(fromDate).toISOString())
		if (toDate) params.set("createdBefore", new Date(toDate).toISOString())

		fetch(`/metrics/token-activity?${params}`, { signal: controller.signal })
			.then(async (res) => {
				if (!res.ok) throw new Error(`HTTP ${res.status}`)
				try {
					const data = (await res.json()) as { items?: TokenActivityRow[] } | null
					return data?.items ?? []
				} catch {
					return []
				}
			})
			.then((items) => setRows(items))
			.catch((err: Error) => {
				if (err.name !== "AbortError") setError(err.message)
			})
			.finally(() => {
				if (!controller.signal.aborted) setLoading(false)
			})

		return () => controller.abort()
	}, [token, fromDate, toDate, tick])

	// Group rows by chain, then by metric label
	const byChain = useMemo(() => {
		const map = new Map<string, Map<string, TokenActivityRow[]>>()
		for (const row of rows) {
			const label = METRIC_LABEL[row.metricType]
			if (!label) continue
			if (!map.has(row.chain)) map.set(row.chain, new Map())
			const chainMap = map.get(row.chain)!
			if (!chainMap.has(label)) chainMap.set(label, [])
			chainMap.get(label)!.push(row)
		}
		// Sort chains alphabetically but put ETHEREUM first
		return Array.from(map.entries()).sort(([a], [b]) => {
			if (a === "ETHEREUM") return -1
			if (b === "ETHEREUM") return 1
			return a.localeCompare(b)
		})
	}, [rows])

	if (loading) return <div className="flex h-44 items-center justify-center text-muted-foreground text-sm">Loading…</div>
	if (error) return <p className="text-destructive text-sm">Failed to load token activity: {error}</p>
	if (byChain.length === 0) return <p className="text-muted-foreground text-sm">No token flow data collected yet.</p>

	return (
		<div className="space-y-10">
			<div className="flex justify-end">
				<DateRangePicker from={fromDate} to={toDate} onChange={onRangeChange} />
			</div>
			{byChain.map(([chain, metricMap]) => {
				const orderedMetrics = METRIC_ORDER.map((label) => ({ label, rows: metricMap.get(label) ?? [] })).filter(({ rows: r }) => r.length > 0)
				return (
					<section key={chain} className="space-y-4">
						<h3 className="text-lg font-semibold tracking-tight">{CHAIN_LABELS[chain] ?? chain}</h3>
						<div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
							{orderedMetrics.map(({ label, rows: metricRows }) => (
								<MetricChart key={label} title={label} rows={metricRows} />
							))}
						</div>
					</section>
				)
			})}
		</div>
	)
}

export default function TokenShow({ token }: PagePropsFor<"token/show">) {
	const tokenStr = token as string
	const displayName = TOKEN_LABELS[tokenStr] ?? tokenStr

	const [fromDate, setFromDate] = useState(defaultFromDate)
	const [toDate, setToDate] = useState("")

	const handleRangeChange = useCallback((f: string, t: string) => {
		setFromDate(f)
		setToDate(t)
	}, [])

	return (
		<>
			<Head title={displayName} />
			<Header title={displayName} icon={<AssetLogo src={TOKEN_LOGOS[tokenStr]} name={displayName} size={32} />} />
			<Container>
				<Tabs defaultValue="token-risk">
					<TabsList>
						<TabsTrigger value="token-risk" className={ACTIVE_TAB_CLASS}>
							Token Risk
						</TabsTrigger>
						<TabsTrigger value="token-flow" className={ACTIVE_TAB_CLASS}>
							Token Flow
						</TabsTrigger>
					</TabsList>
					<TabsContent value="token-risk" className="mt-6">
						<ProtocolMetricsPanel token={tokenStr} emptyMessage={`No manual metrics published for ${displayName} yet.`} />
					</TabsContent>
					<TabsContent value="token-flow" className="mt-6">
						<TokenFlowTab token={tokenStr} fromDate={fromDate} toDate={toDate} onRangeChange={handleRangeChange} />
					</TabsContent>
				</Tabs>
			</Container>
		</>
	)
}

TokenShow.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
