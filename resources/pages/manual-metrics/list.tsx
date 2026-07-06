// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Link, router } from "@inertiajs/react"
import { ArrowUpDown, ChevronDown, ChevronUp } from "lucide-react"
import type React from "react"
import { useCallback, useEffect, useMemo, useState } from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { toast } from "@/components/ui/use-toast"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"

type ManualMetric = PagePropsFor<"manual-metrics/list">["content"]["items"][number]
type TeamOption = PagePropsFor<"manual-metrics/list">["content"]["teams"][number]

const ALL_VALUE = "__all__"

// Entity-type tabs. "chain" / "protocol" / "token" map to the ManualMetric
// entity columns. "market" is a virtual tab: it collects ANCHORS metrics
// pinned to one discovered market (stored under the protocol column with a
// market pin) so they have a dedicated home.
const ENTITY_TYPES = ["chain", "protocol", "token", "market"] as const
type EntityType = (typeof ENTITY_TYPES)[number]

const ENTITY_META: Record<EntityType, { label: string; description: string }> = {
	chain: {
		label: "Chains",
		description: "Metrics scoped to a specific blockchain. Currently limited to the GOVERNANCE category.",
	},
	protocol: {
		label: "Protocols",
		description:
			"Metrics scoped to a specific protocol (e.g. Aave, Morpho). Categories: ANCHORS, CONTROL, ASSURANCE, PROTOCOL_SCORE. Protocol-wide ANCHORS apply to every market of the protocol.",
	},
	token: {
		label: "Tokens",
		description: "Metrics scoped to a specific token (e.g. USDC, WETH). Categories: ANCHORS, CONTROL, ASSURANCE, TOKEN_RISK.",
	},
	market: {
		label: "Markets",
		description: "ANCHORS metrics pinned to one discovered market. Each feeds that market's Probability of Default and shows in its Anchors list.",
	},
}

// The tab a metric belongs to: a market-pinned ANCHORS row lives under
// "Markets" even though the server stores it under the protocol column.
function bucketOf(m: ManualMetric): EntityType {
	if (m.marketChainId != null) return "market"
	return m.entityType as EntityType
}

function shortHex(hex: string): string {
	return hex.length > 14 ? `${hex.slice(0, 8)}…${hex.slice(-4)}` : hex
}

// Litestar's CSRF middleware (cookie XSRF-TOKEN ⇒ header X-XSRF-TOKEN) guards
// unsafe requests. Inertia's router sends this automatically; a raw fetch must
// echo the cookie token itself.
function readCsrfToken(): string | null {
	const m = document.cookie.match(/(?:^|; )XSRF-TOKEN=([^;]+)/)
	return m ? decodeURIComponent(m[1]) : null
}

const RISK_STYLES: Record<number, string> = {
	1: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100",
	2: "bg-lime-100 text-lime-900 dark:bg-lime-900/40 dark:text-lime-100",
	3: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100",
	4: "bg-orange-100 text-orange-900 dark:bg-orange-900/40 dark:text-orange-100",
	5: "bg-rose-100 text-rose-900 dark:bg-rose-900/40 dark:text-rose-100",
}

// Server-derived entityType pairs with exactly one populated column;
// return its value for display in the "Entity" column.
function entityValue(m: ManualMetric): string {
	// Market-pinned rows identify the specific market they target.
	if (m.marketChainId != null) {
		const hex = m.marketIdHex ? shortHex(m.marketIdHex) : ""
		return `${m.protocol ?? "—"} · chain ${m.marketChainId} · ${hex}`
	}
	switch (m.entityType) {
		case "chain":
			return m.chain ?? "—"
		case "protocol":
			return m.protocol ?? "—"
		case "token":
			return m.token ?? "—"
		default:
			return "—"
	}
}

type SortField = "name" | "entity" | "category" | "subCategory" | "value" | "riskScore"
type SortDir = "asc" | "desc"

function sortKey(m: ManualMetric, field: SortField): string | number | null {
	switch (field) {
		case "name":
			return m.name
		case "entity":
			return entityValue(m)
		case "category":
			return m.category
		case "subCategory":
			return m.subCategory ?? null
		case "value":
			return m.value ?? null
		case "riskScore":
			return m.riskScore ?? null
	}
}

function compareMetrics(a: ManualMetric, b: ManualMetric, field: SortField, dir: SortDir): number {
	const sign = dir === "asc" ? 1 : -1
	const av = sortKey(a, field)
	const bv = sortKey(b, field)
	if (av === bv) return 0
	if (av === null || av === "") return 1
	if (bv === null || bv === "") return -1
	if (typeof av === "number" && typeof bv === "number") return (av - bv) * sign
	return String(av).localeCompare(String(bv)) * sign
}

function SortHeader({ label, field, sort, onSort }: { label: string; field: SortField; sort: { field: SortField; dir: SortDir }; onSort: (f: SortField) => void }) {
	const active = sort.field === field
	return (
		<button type="button" className="flex items-center gap-1 font-medium hover:text-foreground" onClick={() => onSort(field)}>
			{label}
			{active ? sort.dir === "asc" ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" /> : <ArrowUpDown className="h-3.5 w-3.5 opacity-40" />}
		</button>
	)
}

function EntityTable({
	entityType,
	items,
	onPublish,
	publishingIds,
}: {
	entityType: EntityType
	items: ManualMetric[]
	onPublish: (id: string) => void
	publishingIds: Set<string>
}) {
	const [search, setSearch] = useState("")
	const [sort, setSort] = useState<{ field: SortField; dir: SortDir }>({ field: "name", dir: "asc" })

	const visible = useMemo(() => {
		const q = search.toLowerCase()
		return items
			.filter((m) => {
				if (!q) return true
				return (
					m.name.toLowerCase().includes(q) ||
					m.desc.toLowerCase().includes(q) ||
					entityValue(m).toLowerCase().includes(q) ||
					m.category.toLowerCase().includes(q) ||
					(m.subCategory ?? "").toLowerCase().includes(q)
				)
			})
			.sort((a, b) => compareMetrics(a, b, sort.field, sort.dir))
	}, [items, search, sort])

	const onSort = (field: SortField) => setSort((s) => ({ field, dir: s.field === field && s.dir === "asc" ? "desc" : "asc" }))

	if (items.length === 0) {
		return <p className="py-8 text-center text-muted-foreground text-sm">No manual metrics for {ENTITY_META[entityType].label.toLowerCase()} in this scope yet.</p>
	}

	return (
		<div className="space-y-4">
			<div className="flex flex-wrap items-center gap-3">
				<Input placeholder="Search name, description, entity, or category…" value={search} onChange={(e) => setSearch(e.target.value)} className="h-8 w-72 text-sm" />
				{search && (
					<button type="button" className="text-muted-foreground text-xs hover:text-foreground" onClick={() => setSearch("")}>
						Clear search
					</button>
				)}
				<span className="ml-auto text-muted-foreground text-xs">
					{visible.length} of {items.length}
				</span>
			</div>

			{visible.length === 0 ? (
				<p className="text-muted-foreground text-sm">No metrics match the current filter.</p>
			) : (
				<div className="rounded-lg border">
					<Table>
						<TableHeader>
							<TableRow>
								<TableHead>
									<SortHeader label="Name" field="name" sort={sort} onSort={onSort} />
								</TableHead>
								<TableHead>
									<SortHeader label={ENTITY_META[entityType].label.slice(0, -1)} field="entity" sort={sort} onSort={onSort} />
								</TableHead>
								<TableHead>
									<SortHeader label="Category" field="category" sort={sort} onSort={onSort} />
								</TableHead>
								<TableHead>
									<SortHeader label="Sub-category" field="subCategory" sort={sort} onSort={onSort} />
								</TableHead>
								<TableHead>
									<SortHeader label="Value" field="value" sort={sort} onSort={onSort} />
								</TableHead>
								<TableHead>
									<SortHeader label="Risk" field="riskScore" sort={sort} onSort={onSort} />
								</TableHead>
								<TableHead className="text-muted-foreground">Scope</TableHead>
								<TableHead className="text-muted-foreground">State</TableHead>
							</TableRow>
						</TableHeader>
						<TableBody>
							{visible.map((m) => {
								const onRowClick = m.canEdit ? () => router.visit(route("manual_metrics.admin.edit_page", { metric_id: m.id })) : undefined
								return (
									<TableRow key={m.id} onClick={onRowClick} className={m.canEdit ? "cursor-pointer hover:bg-muted/50" : undefined} title={m.canEdit ? "Click to edit" : undefined}>
										<TableCell>
											<div>
												<p className="font-medium">{m.name}</p>
												<p className="text-muted-foreground text-xs line-clamp-2">{m.desc}</p>
											</div>
										</TableCell>
										<TableCell>
											<Badge variant="secondary">{entityValue(m)}</Badge>
										</TableCell>
										<TableCell className="text-sm">{m.category}</TableCell>
										<TableCell className="text-muted-foreground text-sm">{m.subCategory ?? "—"}</TableCell>
										<TableCell className="text-sm">{m.value ?? "—"}</TableCell>
										<TableCell>{m.riskScore != null ? <Badge className={RISK_STYLES[m.riskScore]}>Risk {m.riskScore}</Badge> : "—"}</TableCell>
										<TableCell>
											{m.teamId ? (
												<Badge variant="outline" className="text-xs">
													{m.teamName ?? "Team"}
												</Badge>
											) : (
												<Badge variant="secondary" className="text-xs">
													Shared
												</Badge>
											)}
										</TableCell>
										<TableCell>
											<div className="flex items-center gap-2">
												{m.isPublished ? <Badge variant="default">Published</Badge> : <Badge variant="secondary">Draft</Badge>}
												{!m.isPublished && m.canPublish && (
													<Button
														variant="outline"
														size="sm"
														className="h-7 px-2 text-xs"
														disabled={publishingIds.has(m.id)}
														onClick={(e) => {
															e.stopPropagation()
															onPublish(m.id)
														}}
													>
														{publishingIds.has(m.id) ? "Publishing…" : "Publish"}
													</Button>
												)}
											</div>
										</TableCell>
									</TableRow>
								)
							})}
						</TableBody>
					</Table>
				</div>
			)}
		</div>
	)
}

// ─── Page root ────────────────────────────────────────────────────────────────

function initialTabFromQuery(): EntityType {
	if (typeof window === "undefined") return ENTITY_TYPES[0]
	const param = new URLSearchParams(window.location.search).get("entityType") ?? ""
	return (ENTITY_TYPES as readonly string[]).includes(param) ? (param as EntityType) : ENTITY_TYPES[0]
}

export default function ManualMetricsList({ isOperatorEditor, teams, selectedTeamSlug }: PagePropsFor<"manual-metrics/list">["content"]) {
	const teamOptions: TeamOption[] = teams ?? []
	const canCreate = isOperatorEditor || teamOptions.some((t) => t.canEdit && !t.isShared)
	const initialScope = selectedTeamSlug ?? ALL_VALUE

	const [scope, setScope] = useState<string>(initialScope)
	const [items, setItems] = useState<ManualMetric[] | null>(null)
	const [activeTab, setActiveTab] = useState<EntityType>(() => initialTabFromQuery())

	// Single fetch on scope change. We pull a large page (500) and partition
	// client-side by entity type. With the per-row visibility filter applied
	// server-side, the dataset is already narrowed to what the user can see.
	useEffect(() => {
		const controller = new AbortController()
		setItems(null)
		const scopeParam = scope === ALL_VALUE ? "" : `&teamSlug=${encodeURIComponent(scope)}`
		fetch(`/api/manual-metrics?pageSize=500${scopeParam}`, { signal: controller.signal })
			.then((r) => (r.ok ? r.json() : Promise.resolve({ items: [] })))
			.then((data: { items?: ManualMetric[] }) => {
				setItems(data.items ?? [])
			})
			.catch(() => {
				setItems([])
			})
		return () => controller.abort()
	}, [scope])

	const byEntity = useMemo<Record<EntityType, ManualMetric[]>>(() => {
		const buckets: Record<EntityType, ManualMetric[]> = { chain: [], protocol: [], token: [], market: [] }
		for (const m of items ?? []) {
			const et = bucketOf(m)
			if (et in buckets) buckets[et].push(m)
		}
		return buckets
	}, [items])

	const onScopeChange = useCallback((next: string) => {
		setScope(next)
	}, [])

	// Publish a draft in place (the list is client-fetched, so we PATCH the
	// JSON API and flip the row locally rather than navigating away).
	const [publishingIds, setPublishingIds] = useState<Set<string>>(new Set())
	const onPublish = useCallback(async (id: string) => {
		setPublishingIds((prev) => new Set(prev).add(id))
		try {
			const token = readCsrfToken()
			const res = await fetch(`/api/manual-metrics/${id}/publish`, {
				method: "PATCH",
				headers: { "Content-Type": "application/json", ...(token ? { "X-XSRF-TOKEN": token } : {}) },
				credentials: "same-origin",
				body: JSON.stringify({ isPublished: true }),
			})
			if (!res.ok) throw new Error(String(res.status))
			setItems((prev) => (prev ? prev.map((m) => (m.id === id ? { ...m, isPublished: true } : m)) : prev))
			toast({ title: "Published", description: "The metric is now visible.", variant: "success" })
		} catch {
			toast({ title: "Publish failed", description: "Could not publish the metric. Please try again.", variant: "destructive" })
		} finally {
			setPublishingIds((prev) => {
				const next = new Set(prev)
				next.delete(id)
				return next
			})
		}
	}, [])

	const loading = items === null

	return (
		<>
			<Header title="Manual Metrics">
				{canCreate && (
					<Link href={route("manual_metrics.admin.create_page")}>
						<Button size="sm">Add new metric</Button>
					</Link>
				)}
			</Header>
			<Container>
				{teamOptions.length > 0 && (
					<div className="mb-4 flex items-center gap-3">
						<span className="text-muted-foreground text-sm">Scope:</span>
						<Select value={scope} onValueChange={onScopeChange}>
							<SelectTrigger className="h-8 w-64 text-sm">
								<SelectValue placeholder="All scopes" />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value={ALL_VALUE}>All scopes I can see</SelectItem>
								{teamOptions.map((t) => (
									<SelectItem key={t.teamSlug} value={t.teamSlug}>
										{t.teamName}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>
				)}
				<Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as EntityType)}>
					<TabsList className="mb-4 flex h-auto flex-wrap gap-1 bg-transparent p-0">
						{ENTITY_TYPES.map((et) => {
							const count = byEntity[et].length
							return (
								<TabsTrigger key={et} value={et} className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground rounded-md border px-4 py-2 text-sm">
									{ENTITY_META[et].label}
									{!loading && count > 0 && (
										<Badge variant="secondary" className="ml-2 text-xs">
											{count}
										</Badge>
									)}
								</TabsTrigger>
							)
						})}
					</TabsList>

					{ENTITY_TYPES.map((et) => (
						<TabsContent key={et} value={et}>
							<p className="mb-4 text-muted-foreground text-sm">{ENTITY_META[et].description}</p>
							{loading ? (
								<p className="py-8 text-center text-muted-foreground text-sm">Loading…</p>
							) : (
								<EntityTable entityType={et} items={byEntity[et]} onPublish={onPublish} publishingIds={publishingIds} />
							)}
						</TabsContent>
					))}
				</Tabs>
			</Container>
		</>
	)
}

ManualMetricsList.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
