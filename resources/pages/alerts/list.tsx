// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { ArrowUpDown, Bell, ChevronDown, ChevronUp } from "lucide-react"
import type React from "react"
import { useMemo, useState } from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"
import { CreateAlertDialog } from "./partials/create-alert-dialog"

type Alert = PagePropsFor<"alerts/list">["content"]["items"][number]

const ALL = "__all__"

const SEVERITY_STYLES: Record<string, string> = {
	INFO: "bg-sky-100 text-sky-900 dark:bg-sky-900/40 dark:text-sky-100",
	WARNING: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100",
	CRITICAL: "bg-rose-100 text-rose-900 dark:bg-rose-900/40 dark:text-rose-100",
}

type SortField = "name" | "targetKind" | "severity" | "createdAt"
type SortDir = "asc" | "desc"

type Tab = "all" | "templates" | "team"

function targetLabel(alert: Alert): string {
	const cfg = alert.targetConfig as Record<string, unknown>
	if (alert.targetKind === "METRIC") {
		const metric = String(cfg.metricType ?? "")
		const chain = cfg.chain ? ` · ${cfg.chain}` : ""
		const token = cfg.token ? ` · ${cfg.token}` : ""
		return `${metric}${chain}${token}`
	}
	if (alert.targetKind === "MARKET_PD") {
		return `Market PD · chain ${cfg.chainId} · ${String(cfg.marketIdHex).slice(0, 10)}…`
	}
	if (alert.targetKind === "MARKET_ANCHOR") {
		return `Anchor "${cfg.subCategory}" · chain ${cfg.chainId} · ${String(cfg.marketIdHex).slice(0, 10)}…`
	}
	if (alert.targetKind === "MARKET_CONTROL") {
		return `Control "${cfg.subCategory}" · chain ${cfg.chainId} · ${String(cfg.marketIdHex).slice(0, 10)}…`
	}
	return alert.targetKind
}

function compareAlerts(a: Alert, b: Alert, field: SortField, dir: SortDir): number {
	const sign = dir === "asc" ? 1 : -1
	if (field === "targetKind") {
		return targetLabel(a).localeCompare(targetLabel(b)) * sign
	}
	const av = (a[field] ?? "") as string | number
	const bv = (b[field] ?? "") as string | number
	if (av === bv) return 0
	if (!av) return 1
	if (!bv) return -1
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

export default function AlertsList({ items, total, isOperatorEditor, isTeamEditor }: PagePropsFor<"alerts/list">["content"]) {
	const [tab, setTab] = useState<Tab>("all")
	const [search, setSearch] = useState("")
	const [targetKindFilter, setTargetKindFilter] = useState(ALL)
	const [severityFilter, setSeverityFilter] = useState(ALL)
	const [sort, setSort] = useState<{ field: SortField; dir: SortDir }>({ field: "createdAt", dir: "desc" })

	const onSort = (field: SortField) => setSort((s) => ({ field, dir: s.field === field && s.dir === "asc" ? "desc" : "asc" }))

	const visible = useMemo(() => {
		const q = search.toLowerCase()
		return items
			.filter((a) => {
				if (tab === "templates" && !a.isTemplate) return false
				if (tab === "team" && a.isTemplate) return false
				if (targetKindFilter !== ALL && a.targetKind !== targetKindFilter) return false
				if (severityFilter !== ALL && a.severity !== severityFilter) return false
				if (!q) return true
				return a.name.toLowerCase().includes(q) || a.description.toLowerCase().includes(q) || targetLabel(a).toLowerCase().includes(q)
			})
			.sort((a, b) => compareAlerts(a, b, sort.field, sort.dir))
	}, [items, tab, search, targetKindFilter, severityFilter, sort])

	const tabCounts = useMemo(
		() => ({
			all: items.length,
			templates: items.filter((a) => a.isTemplate).length,
			team: items.filter((a) => !a.isTemplate).length,
		}),
		[items],
	)

	const hasFilters = search || targetKindFilter !== ALL || severityFilter !== ALL
	const clearFilters = () => {
		setSearch("")
		setTargetKindFilter(ALL)
		setSeverityFilter(ALL)
	}

	return (
		<>
			<Head title="Alerts" />
			<Header title="Alerts" icon={<Bell className="h-5 w-5" />}>
				<CreateAlertDialog isOperatorEditor={isOperatorEditor} isTeamEditor={isTeamEditor} />
			</Header>
			<Container>
				<Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
					<TabsList className="mb-4 flex h-auto flex-wrap gap-1 bg-transparent p-0">
						<TabsTrigger value="all" className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground rounded-md border px-4 py-2 text-sm">
							All{" "}
							<Badge variant="secondary" className="ml-2 text-xs">
								{tabCounts.all}
							</Badge>
						</TabsTrigger>
						<TabsTrigger value="templates" className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground rounded-md border px-4 py-2 text-sm">
							Operator Templates{" "}
							<Badge variant="secondary" className="ml-2 text-xs">
								{tabCounts.templates}
							</Badge>
						</TabsTrigger>
						<TabsTrigger value="team" className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground rounded-md border px-4 py-2 text-sm">
							My Team{" "}
							<Badge variant="secondary" className="ml-2 text-xs">
								{tabCounts.team}
							</Badge>
						</TabsTrigger>
					</TabsList>
				</Tabs>

				<div className="mb-4 flex flex-wrap items-center gap-3">
					<Input placeholder="Search name, description, or target…" value={search} onChange={(e) => setSearch(e.target.value)} className="h-8 w-72 text-sm" />
					<Select value={targetKindFilter} onValueChange={setTargetKindFilter}>
						<SelectTrigger className="h-8 w-44 text-sm">
							<SelectValue placeholder="Target kind" />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value={ALL}>Any target</SelectItem>
							<SelectItem value="METRIC">Metric</SelectItem>
							<SelectItem value="MARKET_PD">Market PD</SelectItem>
							<SelectItem value="MARKET_ANCHOR">Market anchor</SelectItem>
							<SelectItem value="MARKET_CONTROL">Market control</SelectItem>
						</SelectContent>
					</Select>
					<Select value={severityFilter} onValueChange={setSeverityFilter}>
						<SelectTrigger className="h-8 w-36 text-sm">
							<SelectValue placeholder="Severity" />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value={ALL}>Any severity</SelectItem>
							<SelectItem value="INFO">Info</SelectItem>
							<SelectItem value="WARNING">Warning</SelectItem>
							<SelectItem value="CRITICAL">Critical</SelectItem>
						</SelectContent>
					</Select>
					{hasFilters && (
						<button type="button" className="text-muted-foreground text-xs hover:text-foreground" onClick={clearFilters}>
							Clear filters
						</button>
					)}
					<span className="ml-auto text-muted-foreground text-xs">
						{visible.length} of {total}
					</span>
				</div>

				{total === 0 ? (
					<Card className="flex flex-col items-center justify-center py-12">
						<p className="font-semibold text-lg">No alerts yet</p>
						<p className="mt-2 text-muted-foreground text-sm">Operator templates and team-defined alerts will appear here.</p>
					</Card>
				) : visible.length === 0 ? (
					<p className="text-muted-foreground text-sm">No alerts match the current filters.</p>
				) : (
					<Card>
						<Table>
							<TableHeader>
								<TableRow>
									<TableHead>
										<SortHeader label="Name" field="name" sort={sort} onSort={onSort} />
									</TableHead>
									<TableHead>
										<SortHeader label="Target" field="targetKind" sort={sort} onSort={onSort} />
									</TableHead>
									<TableHead>
										<SortHeader label="Severity" field="severity" sort={sort} onSort={onSort} />
									</TableHead>
									<TableHead className="text-muted-foreground">Status</TableHead>
									<TableHead className="text-muted-foreground">Source</TableHead>
								</TableRow>
							</TableHeader>
							<TableBody>
								{visible.map((a) => (
									<TableRow key={a.id}>
										<TableCell>
											<Link href={route("alerts.history", { alert_id: a.id })} className="font-medium hover:underline">
												{a.name}
											</Link>
											<p className="text-muted-foreground text-xs line-clamp-2">{a.description}</p>
										</TableCell>
										<TableCell className="font-mono text-xs">
											<Badge variant="outline" className="mr-1">
												{a.targetKind}
											</Badge>
											<span>{targetLabel(a)}</span>
										</TableCell>
										<TableCell>
											<Badge className={SEVERITY_STYLES[a.severity]}>{a.severity}</Badge>
										</TableCell>
										<TableCell>
											{a.isEnabled ? (
												<Badge variant="outline" className="text-emerald-700 dark:text-emerald-300">
													Enabled
												</Badge>
											) : (
												<Badge variant="outline" className="text-muted-foreground">
													Disabled
												</Badge>
											)}
										</TableCell>
										<TableCell className="text-muted-foreground text-xs">{a.isTemplate ? "Operator template" : "Team-defined"}</TableCell>
									</TableRow>
								))}
							</TableBody>
						</Table>
					</Card>
				)}
			</Container>
		</>
	)
}

AlertsList.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
