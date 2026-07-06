// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link, router } from "@inertiajs/react"
import { ArrowUpDown, ChevronDown, ChevronUp, Download, Trash2, Upload } from "lucide-react"
import type React from "react"
import { useState } from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"

type SecurityReport = PagePropsFor<"security-reports/list">["content"]["items"][number]

type SortField = "name" | "uploaderName" | "createdAt"
type SortDir = "asc" | "desc"

function SortHeader({ label, field, sort, onSort }: { label: string; field: SortField; sort: { field: SortField; dir: SortDir }; onSort: (f: SortField) => void }) {
	const active = sort.field === field
	return (
		<button type="button" className="flex items-center gap-1 font-medium hover:text-foreground" onClick={() => onSort(field)}>
			{label}
			{active ? sort.dir === "asc" ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" /> : <ArrowUpDown className="h-3.5 w-3.5 opacity-40" />}
		</button>
	)
}

function compareReports(a: SecurityReport, b: SecurityReport, field: SortField, dir: SortDir): number {
	const sign = dir === "asc" ? 1 : -1
	const av = a[field] ?? ""
	const bv = b[field] ?? ""
	if (av === bv) return 0
	if (!av) return 1
	if (!bv) return -1
	return String(av).localeCompare(String(bv)) * sign
}

export default function SecurityReportsList({ items, total, isOperatorEditor }: PagePropsFor<"security-reports/list">["content"]) {
	const [search, setSearch] = useState("")
	const [sort, setSort] = useState<{ field: SortField; dir: SortDir }>({ field: "createdAt", dir: "desc" })

	const onSort = (field: SortField) => setSort((s) => ({ field, dir: s.field === field && s.dir === "asc" ? "desc" : "asc" }))

	const handleDelete = (id: string, name: string) => {
		if (!window.confirm(`Delete security report "${name}"? This cannot be undone.`)) return
		router.delete(route("security_reports.admin.delete", { report_id: id }), {
			preserveScroll: true,
			onSuccess: () => {
				toast({ title: "Report deleted", description: `Removed "${name}".`, variant: "success" })
			},
		})
	}

	const visible = items
		.filter((r) => {
			const q = search.toLowerCase()
			return !q || r.name.toLowerCase().includes(q) || r.description.toLowerCase().includes(q)
		})
		.sort((a, b) => compareReports(a, b, sort.field, sort.dir))

	return (
		<>
			<Head title="Security Reports" />
			<Header title="Security Reports">
				{isOperatorEditor && (
					<Link href={route("security_reports.admin.upload_page")}>
						<Button size="sm">
							<Upload className="mr-2 h-4 w-4" />
							Upload report
						</Button>
					</Link>
				)}
			</Header>
			<Container>
				<div className="mb-4 flex items-center gap-3">
					<Input placeholder="Search reports…" value={search} onChange={(e) => setSearch(e.target.value)} className="h-8 w-64 text-sm" />
					{search && (
						<button type="button" className="text-muted-foreground text-xs hover:text-foreground" onClick={() => setSearch("")}>
							Clear
						</button>
					)}
					<span className="ml-auto text-muted-foreground text-xs">
						{visible.length} of {total}
					</span>
				</div>

				{total === 0 ? (
					<Card className="flex flex-col items-center justify-center py-12">
						<p className="font-semibold text-lg">No security reports yet</p>
						<p className="mt-2 text-muted-foreground text-sm">Reports will appear here once uploaded by the operator team.</p>
					</Card>
				) : visible.length === 0 ? (
					<p className="text-muted-foreground text-sm">No reports match the current search.</p>
				) : (
					<Card>
						<Table>
							<TableHeader>
								<TableRow>
									<TableHead>
										<SortHeader label="Name" field="name" sort={sort} onSort={onSort} />
									</TableHead>
									<TableHead>Description</TableHead>
									<TableHead>
										<SortHeader label="Uploaded by" field="uploaderName" sort={sort} onSort={onSort} />
									</TableHead>
									<TableHead>
										<SortHeader label="Date" field="createdAt" sort={sort} onSort={onSort} />
									</TableHead>
									<TableHead className="text-right">{isOperatorEditor ? "Actions" : "Download"}</TableHead>
								</TableRow>
							</TableHeader>
							<TableBody>
								{visible.map((r) => (
									<TableRow key={r.id}>
										<TableCell className="font-medium">{r.name}</TableCell>
										<TableCell className="text-muted-foreground text-sm max-w-xs truncate">{r.description}</TableCell>
										<TableCell className="text-sm">{r.uploaderName ?? "—"}</TableCell>
										<TableCell className="text-muted-foreground text-sm">{r.createdAt ? new Date(r.createdAt).toLocaleDateString() : "—"}</TableCell>
										<TableCell className="text-right">
											<div className="flex items-center justify-end gap-1">
												<a href={r.fileUrl} download={`${r.name}.pdf`}>
													<Button variant="ghost" size="sm">
														<Download className="h-4 w-4" />
														<span className="sr-only">Download {r.name}</span>
													</Button>
												</a>
												{isOperatorEditor && (
													<Button variant="ghost" size="sm" onClick={() => handleDelete(r.id, r.name)} className="text-destructive hover:text-destructive">
														<Trash2 className="h-4 w-4" />
														<span className="sr-only">Delete {r.name}</span>
													</Button>
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

SecurityReportsList.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
