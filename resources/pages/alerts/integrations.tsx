// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { ArrowLeft, Mail, Webhook } from "lucide-react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"

type Integration = PagePropsFor<"alerts/integrations">["content"]["items"][number]

function kindIcon(kind: Integration["kind"]) {
	switch (kind) {
		case "EMAIL":
			return <Mail className="h-3.5 w-3.5" />
		case "WEBHOOK":
			return <Webhook className="h-3.5 w-3.5" />
		default:
			return null
	}
}

function destinationLabel(integration: Integration): string {
	const config = integration.config
	if (config.type === "EMAIL") {
		return config.cc.length > 0 ? `${config.to} (+${config.cc.length} cc)` : config.to
	}
	if (config.type === "WEBHOOK") {
		return config.url
	}
	return "—"
}

export default function AlertsIntegrations({ items, total }: PagePropsFor<"alerts/integrations">["content"]) {
	return (
		<>
			<Head title="Alert integrations" />
			<Header title="Alert integrations">
				<Link href={route("alerts")} className="flex items-center gap-1 text-muted-foreground text-sm hover:text-foreground">
					<ArrowLeft className="h-4 w-4" />
					Back to alerts
				</Link>
			</Header>
			<Container>
				<p className="mb-4 text-muted-foreground text-sm max-w-2xl">
					Notification channels attached to your team. Each team has at most one <strong>primary</strong> channel per kind; alerts can attach additional channels via the per-alert
					setting.
				</p>

				<div className="mb-3 flex items-center justify-between">
					<span className="text-muted-foreground text-xs">
						{total} {total === 1 ? "integration" : "integrations"}
					</span>
				</div>

				{total === 0 ? (
					<Card className="flex flex-col items-center justify-center py-12">
						<Mail className="mb-2 h-8 w-8 text-muted-foreground" />
						<p className="font-semibold text-base">No integrations yet</p>
						<p className="mt-1 text-muted-foreground text-sm text-center max-w-md">A default email integration will be created automatically when your team is provisioned.</p>
					</Card>
				) : (
					<Card>
						<Table>
							<TableHeader>
								<TableRow>
									<TableHead>Name</TableHead>
									<TableHead>Kind</TableHead>
									<TableHead>Destination</TableHead>
									<TableHead className="text-muted-foreground">Primary</TableHead>
									<TableHead className="text-muted-foreground">Status</TableHead>
								</TableRow>
							</TableHeader>
							<TableBody>
								{items.map((integration) => (
									<TableRow key={integration.id}>
										<TableCell className="font-medium">{integration.name}</TableCell>
										<TableCell>
											<Badge variant="secondary" className="gap-1">
												{kindIcon(integration.kind)}
												{integration.kind}
											</Badge>
										</TableCell>
										<TableCell className="font-mono text-xs max-w-md truncate">{destinationLabel(integration)}</TableCell>
										<TableCell>
											{integration.isPrimary ? (
												<Badge variant="outline" className="text-emerald-700 dark:text-emerald-300">
													Primary
												</Badge>
											) : (
												"—"
											)}
										</TableCell>
										<TableCell>
											{integration.isActive ? (
												<Badge variant="outline" className="text-emerald-700 dark:text-emerald-300">
													Active
												</Badge>
											) : (
												<Badge variant="outline" className="text-muted-foreground">
													Disabled
												</Badge>
											)}
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

AlertsIntegrations.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
