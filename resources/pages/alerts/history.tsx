// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { AlertTriangle, ArrowLeft, CheckCircle2, Clock, XCircle } from "lucide-react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"

type HistoryRow = PagePropsFor<"alerts/history">["content"]["items"][number]
type AlertProp = PagePropsFor<"alerts/history">["content"]["alert"]

function targetLabel(alert: AlertProp): string {
	const cfg = alert.targetConfig as Record<string, unknown>
	if (alert.targetKind === "METRIC") {
		const metric = String(cfg.metricType ?? "")
		const chain = cfg.chain ? ` · ${cfg.chain}` : ""
		const token = cfg.token ? ` · ${cfg.token}` : ""
		return `${metric}${chain}${token}`
	}
	if (alert.targetKind === "MARKET_PD") {
		return `Market PD · chain ${cfg.chainId} · ${String(cfg.marketIdHex).slice(0, 14)}…`
	}
	if (alert.targetKind === "MARKET_ANCHOR") {
		return `Anchor "${cfg.subCategory}" · chain ${cfg.chainId} · ${String(cfg.marketIdHex).slice(0, 14)}…`
	}
	if (alert.targetKind === "MARKET_CONTROL") {
		return `Control "${cfg.subCategory}" · chain ${cfg.chainId} · ${String(cfg.marketIdHex).slice(0, 14)}…`
	}
	return alert.targetKind
}

const STATUS_STYLES: Record<string, string> = {
	OK: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100",
	TRIGGERED: "bg-rose-100 text-rose-900 dark:bg-rose-900/40 dark:text-rose-100",
	RECOVERED: "bg-sky-100 text-sky-900 dark:bg-sky-900/40 dark:text-sky-100",
	ERROR: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100",
}

function statusIcon(status: HistoryRow["status"]) {
	switch (status) {
		case "TRIGGERED":
			return <AlertTriangle className="h-3.5 w-3.5" />
		case "RECOVERED":
			return <CheckCircle2 className="h-3.5 w-3.5" />
		case "ERROR":
			return <XCircle className="h-3.5 w-3.5" />
		default:
			return <Clock className="h-3.5 w-3.5" />
	}
}

function formatDateTime(iso: string): string {
	const d = new Date(iso)
	return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

export default function AlertsHistory({ alert, items, total }: PagePropsFor<"alerts/history">["content"]) {
	return (
		<>
			<Head title={`History — ${alert.name}`} />
			<Header title="Alert history">
				<Link href={route("alerts")} className="flex items-center gap-1 text-muted-foreground text-sm hover:text-foreground">
					<ArrowLeft className="h-4 w-4" />
					Back to alerts
				</Link>
			</Header>
			<Container>
				<Card className="mb-6 p-6">
					<div className="flex flex-wrap items-start justify-between gap-4">
						<div className="space-y-1">
							<h2 className="font-semibold text-lg">{alert.name}</h2>
							<p className="text-muted-foreground text-sm">{alert.description}</p>
							<div className="mt-2 flex flex-wrap gap-2">
								<Badge variant="secondary">{alert.targetKind}</Badge>
								<Badge variant="secondary" className="font-mono">
									{targetLabel(alert)}
								</Badge>
								<Badge variant="outline">{alert.ruleKind}</Badge>
								<Badge variant="outline">{alert.severity}</Badge>
								{alert.isTemplate && <Badge variant="outline">Operator template</Badge>}
							</div>
						</div>
						<div className="text-muted-foreground text-xs">{alert.isEnabled ? "Enabled" : "Disabled"}</div>
					</div>
				</Card>

				<div className="mb-3 flex items-center justify-between">
					<h3 className="font-semibold text-sm">Recent events</h3>
					<span className="text-muted-foreground text-xs">
						{total} {total === 1 ? "event" : "events"}
					</span>
				</div>

				{total === 0 ? (
					<Card className="flex flex-col items-center justify-center py-12">
						<Clock className="mb-2 h-8 w-8 text-muted-foreground" />
						<p className="font-semibold text-base">No history yet</p>
						<p className="mt-1 text-muted-foreground text-sm text-center max-w-md">Events will appear here once the evaluator detects state changes for this alert.</p>
					</Card>
				) : (
					<Card>
						<Table>
							<TableHeader>
								<TableRow>
									<TableHead>Status</TableHead>
									<TableHead>When</TableHead>
									<TableHead>Value</TableHead>
									<TableHead>Threshold</TableHead>
									<TableHead className="text-muted-foreground">Message</TableHead>
								</TableRow>
							</TableHeader>
							<TableBody>
								{items.map((row) => (
									<TableRow key={row.id}>
										<TableCell>
											<Badge className={`gap-1 ${STATUS_STYLES[row.status] ?? ""}`}>
												{statusIcon(row.status)}
												{row.status}
											</Badge>
										</TableCell>
										<TableCell className="text-sm">{formatDateTime(row.evaluatedAt)}</TableCell>
										<TableCell className="font-mono text-sm">{row.metricValue ?? "—"}</TableCell>
										<TableCell className="font-mono text-sm">{row.threshold ?? "—"}</TableCell>
										<TableCell className="text-muted-foreground text-xs max-w-md truncate">{row.message ?? row.context.notes ?? "—"}</TableCell>
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

AlertsHistory.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
