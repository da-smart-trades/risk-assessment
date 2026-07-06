// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link, router } from "@inertiajs/react"
import { Pencil, Plus, Trash2 } from "lucide-react"
import type React from "react"
import { useCallback } from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"

const ALL_VALUE = "__all__"

export default function ManualMetricsAdminList({ items, total, teams, selectedTeamSlug }: PagePropsFor<"manual-metrics/admin/list">["content"]) {
	const scopeValue = selectedTeamSlug ?? ALL_VALUE

	const onScopeChange = useCallback((next: string) => {
		const url = next === ALL_VALUE ? route("manual_metrics.admin.list") : `${route("manual_metrics.admin.list")}?teamSlug=${encodeURIComponent(next)}`
		router.visit(url, { preserveScroll: true, preserveState: false })
	}, [])

	const handleDelete = (id: string, name: string) => {
		if (!window.confirm(`Delete manual metric "${name}"? This cannot be undone.`)) {
			return
		}
		router.delete(route("manual_metrics.admin.delete", { metric_id: id }), {
			preserveScroll: true,
			onSuccess: () => {
				toast({
					title: "Manual metric deleted",
					description: `Removed "${name}".`,
					variant: "success",
				})
			},
		})
	}

	return (
		<>
			<Head title="Manual Metrics — Manage" />
			<Header title="Manual Metrics — Manage">
				<Link href={route("manual_metrics.admin.create_page")}>
					<Button>
						<Plus className="mr-2 h-4 w-4" />
						New metric
					</Button>
				</Link>
			</Header>
			<Container>
				{teams.length > 0 && (
					<div className="mb-4 flex items-center gap-3">
						<span className="text-muted-foreground text-sm">Scope:</span>
						<Select value={scopeValue} onValueChange={onScopeChange}>
							<SelectTrigger className="h-8 w-64 text-sm">
								<SelectValue placeholder="All scopes I can edit" />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value={ALL_VALUE}>All scopes I can edit</SelectItem>
								{teams.map((t) => (
									<SelectItem key={t.teamSlug} value={t.teamSlug}>
										{t.teamName}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>
				)}
				{total === 0 ? (
					<Card>
						<CardContent className="flex flex-col items-center justify-center py-12">
							<h3 className="font-semibold text-lg">No manual metrics yet</h3>
							<p className="mt-2 text-muted-foreground text-sm">Create the first manual metric to start the catalogue.</p>
							<Link href={route("manual_metrics.admin.create_page")} className="mt-4">
								<Button>
									<Plus className="mr-2 h-4 w-4" />
									New metric
								</Button>
							</Link>
						</CardContent>
					</Card>
				) : (
					<Card>
						<Table>
							<TableHeader>
								<TableRow>
									<TableHead>Name</TableHead>
									<TableHead>Scope</TableHead>
									<TableHead>Category</TableHead>
									<TableHead>Chain</TableHead>
									<TableHead>Token</TableHead>
									<TableHead>Risk</TableHead>
									<TableHead className="text-right">Actions</TableHead>
								</TableRow>
							</TableHeader>
							<TableBody>
								{items.map((m) => (
									<TableRow key={m.id}>
										<TableCell className="font-medium">{m.name}</TableCell>
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
											{m.category}
											{m.subCategory ? ` / ${m.subCategory}` : ""}
										</TableCell>
										<TableCell>{m.chain ?? "—"}</TableCell>
										<TableCell>{m.token ?? "—"}</TableCell>
										<TableCell>{m.riskScore ?? "—"}</TableCell>
										<TableCell className="text-right">
											<div className="flex justify-end gap-2">
												{m.canEdit ? (
													<>
														<Link href={route("manual_metrics.admin.edit_page", { metric_id: m.id })}>
															<Button variant="ghost" size="sm">
																<Pencil className="h-4 w-4" />
															</Button>
														</Link>
														<Button variant="ghost" size="sm" onClick={() => handleDelete(m.id, m.name)}>
															<Trash2 className="h-4 w-4" />
														</Button>
													</>
												) : (
													<span className="text-muted-foreground text-xs">Read-only</span>
												)}
											</div>
										</TableCell>
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

ManualMetricsAdminList.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
