// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { Plus } from "lucide-react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { AdminLayout } from "@/layouts/admin-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"

export default function WeightingProfilesList({ profiles, total }: PagePropsFor<"admin/weighting-profiles/list">) {
	return (
		<>
			<Head title="Weighting profiles" />
			<Header
				title="Weighting profiles"
				subtitle={`${total} ${total === 1 ? "profile" : "profiles"}`}
				actions={
					<Button asChild>
						<Link href={route("admin.weighting_profiles.create_page")}>
							<Plus className="h-4 w-4 mr-1" />
							New profile
						</Link>
					</Button>
				}
			/>
			<Container>
				{profiles.length === 0 ? (
					<EmptyState />
				) : (
					<div className="grid grid-cols-1 gap-3">
						{profiles.map((p) => (
							<Link key={p.id} href={route("admin.weighting_profiles.edit_page", { profile_id: p.id })}>
								<Card className="transition-shadow hover:shadow-md">
									<CardContent className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 py-4">
										<div className="min-w-0">
											<div className="flex items-center flex-wrap gap-2">
												<span className="text-sm font-medium text-foreground">{p.name}</span>
												<Badge variant="outline">{p.scope === "MARKET" ? "Market" : "Protocol"}</Badge>
												{p.teamName ? <Badge variant="secondary">{p.teamName}</Badge> : <Badge>Global default</Badge>}
											</div>
											<p className="text-xs text-muted-foreground truncate">
												Target: {p.scope === "MARKET" ? (p.targetMarketLabel ?? p.targetMarketConfigId) : p.targetProtocol}
												{" · "}
												{p.entries.length} {p.entries.length === 1 ? "override" : "overrides"}
											</p>
										</div>
									</CardContent>
								</Card>
							</Link>
						))}
					</div>
				)}
			</Container>
		</>
	)
}

function EmptyState() {
	return (
		<Card>
			<CardHeader>
				<CardTitle>No weighting profiles yet</CardTitle>
				<CardDescription>Every (category, sub_category) defaults to weight 1.0. Create a profile to override specific weights for a market or protocol.</CardDescription>
			</CardHeader>
		</Card>
	)
}

WeightingProfilesList.layout = (page: React.ReactNode) => <AdminLayout>{page}</AdminLayout>
