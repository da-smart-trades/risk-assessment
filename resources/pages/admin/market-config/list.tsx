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

export default function MarketConfigList({ markets, total }: PagePropsFor<"admin/market-config/list">) {
	return (
		<>
			<Head title="Protocols" />
			<Header
				title="Protocols"
				subtitle={`${total} ${total === 1 ? "protocol" : "protocols"}`}
				actions={
					<Button asChild>
						<Link href={route("admin.market_config.create_page")}>
							<Plus className="h-4 w-4 mr-1" />
							Add protocol
						</Link>
					</Button>
				}
			/>
			<Container>
				{markets.length === 0 ? (
					<EmptyState />
				) : (
					<div className="grid grid-cols-1 gap-3">
						{markets.map((m) => (
							<Link key={m.id} href={route("admin.market_config.edit_page", { market_config_id: m.id })}>
								<Card className="transition-shadow hover:shadow-md">
									<CardContent className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 py-4">
										<div className="min-w-0">
											<div className="flex items-center gap-2">
												<span className="text-sm font-medium text-foreground font-mono">{m.protocol}</span>
												{!m.enabled && <Badge variant="secondary">Disabled</Badge>}
											</div>
											<p className="text-xs text-muted-foreground">
												Markets discovered live via <code>yarn {m.protocol}</code> on every tick.
											</p>
										</div>
										<div className="flex items-center gap-3">
											{m.createdAt && <span className="text-xs text-muted-foreground">Added {new Date(m.createdAt).toLocaleDateString()}</span>}
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
				<CardTitle>No protocols yet</CardTitle>
				<CardDescription>
					Add the first protocol via the "Add protocol" button. Once enabled, the collector runs <code>yarn &lt;protocol&gt;</code> within five minutes to discover its markets and
					starts producing snapshots; the scorer follows hourly.
				</CardDescription>
			</CardHeader>
		</Card>
	)
}

MarketConfigList.layout = (page: React.ReactNode) => <AdminLayout>{page}</AdminLayout>
