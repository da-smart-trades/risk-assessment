// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link, router } from "@inertiajs/react"
import { Plus, Trash2 } from "lucide-react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"

export default function SecurityReportsAdminList({ items, total }: PagePropsFor<"security-reports/admin/list">["content"]) {
	const handleDelete = (id: string, name: string) => {
		if (!window.confirm(`Delete security report "${name}"? This cannot be undone.`)) return
		router.delete(route("security_reports.admin.delete", { report_id: id }), {
			preserveScroll: true,
			onSuccess: () => {
				toast({ title: "Report deleted", description: `Removed "${name}".`, variant: "success" })
			},
		})
	}

	return (
		<>
			<Head title="Security Reports — Operator" />
			<Header title="Security Reports — Operator">
				<Link href={route("security_reports.admin.upload_page")}>
					<Button>
						<Plus className="mr-2 h-4 w-4" />
						Upload report
					</Button>
				</Link>
			</Header>
			<Container>
				{total === 0 ? (
					<Card>
						<CardContent className="flex flex-col items-center justify-center py-12">
							<h3 className="font-semibold text-lg">No security reports yet</h3>
							<p className="mt-2 text-muted-foreground text-sm">Upload the first report to publish it for your team.</p>
							<Link href={route("security_reports.admin.upload_page")} className="mt-4">
								<Button>
									<Plus className="mr-2 h-4 w-4" />
									Upload report
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
									<TableHead>Description</TableHead>
									<TableHead>Uploaded by</TableHead>
									<TableHead>Date</TableHead>
									<TableHead className="text-right">Actions</TableHead>
								</TableRow>
							</TableHeader>
							<TableBody>
								{items.map((r) => (
									<TableRow key={r.id}>
										<TableCell className="font-medium">{r.name}</TableCell>
										<TableCell className="text-muted-foreground text-sm max-w-xs truncate">{r.description}</TableCell>
										<TableCell className="text-sm">{r.uploaderName ?? "—"}</TableCell>
										<TableCell className="text-muted-foreground text-sm">{r.createdAt ? new Date(r.createdAt).toLocaleDateString() : "—"}</TableCell>
										<TableCell className="text-right">
											<Button variant="ghost" size="sm" onClick={() => handleDelete(r.id, r.name)} className="text-destructive hover:text-destructive">
												<Trash2 className="h-4 w-4" />
												<span className="sr-only">Delete</span>
											</Button>
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

SecurityReportsAdminList.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
