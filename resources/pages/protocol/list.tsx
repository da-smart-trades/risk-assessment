// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"

const PROTOCOL_LABELS: Record<string, string> = {
	AAVE_V3: "Aave v3",
	MORPHO_V2: "Morpho v2",
	COMPOUND_V3: "Compound v3",
	DRIFT_V2: "Drift v2",
}

export default function ProtocolList({ protocols }: PagePropsFor<"protocol/list">) {
	return (
		<>
			<Head title="Protocols" />
			<Header title="Protocols" />
			<Container>
				<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
					{protocols.map((protocol) => (
						<Link key={protocol} href={route("protocols.show", { protocol_name: protocol })}>
							<Card className="transition-shadow hover:shadow-md">
								<CardHeader>
									<div className="flex items-center gap-3">
										<CardTitle className="text-lg">{PROTOCOL_LABELS[protocol] ?? protocol}</CardTitle>
									</div>
								</CardHeader>
								<CardContent>
									<p className="text-muted-foreground text-sm">{protocol}</p>
								</CardContent>
							</Card>
						</Link>
					))}
				</div>
			</Container>
		</>
	)
}

ProtocolList.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
