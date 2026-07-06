// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import type React from "react"
import { AssetLogo } from "@/components/asset-logo"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"
import { CHAIN_LOGOS } from "@/lib/logos"

const CHAIN_LABELS: Record<string, string> = {
	ARBITRUM: "Arbitrum",
	ETHEREUM: "Ethereum",
	SOLANA: "Solana",
	BASE: "Base",
	INK: "Ink",
	UNICHAIN: "Unichain",
	POLYGON: "Polygon",
	AVALANCHE_C: "Avalanche C",
	OPTIMISM: "Optimism",
	CANTON: "Canton",
}

export default function ChainList({ chains }: PagePropsFor<"chain/list">) {
	return (
		<>
			<Head title="Block Chains" />
			<Header title="Block Chains" />
			<Container>
				<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
					{chains.map((chain) => (
						<Link key={chain} href={route("chains.show", { chain_name: chain })}>
							<Card className="transition-shadow hover:shadow-md">
								<CardHeader>
									<div className="flex items-center gap-3">
										<AssetLogo src={CHAIN_LOGOS[chain]} name={chain} size={28} />
										<CardTitle className="text-lg">{CHAIN_LABELS[chain] ?? chain}</CardTitle>
									</div>
								</CardHeader>
								<CardContent>
									<p className="text-muted-foreground text-sm">{chain}</p>
								</CardContent>
							</Card>
						</Link>
					))}
				</div>
			</Container>
		</>
	)
}

ChainList.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
