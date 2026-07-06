// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import type React from "react"
import { useMemo, useState } from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { AppLayout } from "@/layouts/app-layout"
import { formatChainId } from "@/lib/chain-labels"
import { formatFinalPd, formatMarketLabel } from "@/lib/format-evidence"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"

export default function MarketList({ markets }: PagePropsFor<"market/list">) {
	const [query, setQuery] = useState("")

	// Containment search across everything the card shows: the raw label,
	// the formatted label, the protocol, and the chain name. We strip all
	// separators (spaces, hyphens, underscores) from both the query and the
	// haystack before matching, so typing any form — "USDCMarket",
	// "USDC_MARKET", "usdc market", "aave-v3", "base" — hits.
	const filtered = useMemo(() => {
		const normalize = (s: string) => s.toLowerCase().replace(/[\s_-]+/g, "")
		const needle = normalize(query)
		if (needle === "") return markets
		return markets.filter((market) => {
			const haystack = normalize([market.label, formatMarketLabel(market.label), market.protocol, formatChainId(market.chainId)].join(" "))
			return haystack.includes(needle)
		})
	}, [markets, query])

	return (
		<>
			<Head title="Markets" />
			<Header title="Markets" />
			<Container>
				{markets.length === 0 ? (
					<EmptyState />
				) : (
					<div className="space-y-4">
						<Input
							type="search"
							value={query}
							onChange={(e) => setQuery(e.target.value)}
							placeholder="Search markets by label…"
							aria-label="Search markets by label"
							className="max-w-sm"
						/>
						{filtered.length === 0 ? (
							<p className="text-sm text-muted-foreground">No markets match “{query.trim()}”.</p>
						) : (
							<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
								{filtered.map((market) => (
									<Link
										key={market.id}
										href={route("markets.show", {
											protocol: market.protocol,
											chain_id: market.chainId,
											market_id_hex: market.marketIdHex,
										})}
									>
										<Card className="h-full transition-shadow hover:shadow-md">
											<CardHeader>
												<div className="flex items-start justify-between gap-3">
													<div className="min-w-0">
														<CardTitle className="text-lg truncate">{formatMarketLabel(market.label)}</CardTitle>
														<CardDescription className="truncate">
															{market.protocol} · {formatChainId(market.chainId)}
														</CardDescription>
													</div>
													{!market.enabled && <Badge variant="secondary">Disabled</Badge>}
												</div>
											</CardHeader>
											<CardContent>
												<div className="flex flex-col gap-1">
													<span className="text-xs font-medium text-muted-foreground">Probability of Default</span>
													<span className="text-3xl font-semibold tabular-nums">{formatFinalPd(market.latestPd)}</span>
													{market.latestPdAt && <span className="text-xs text-muted-foreground">{new Date(market.latestPdAt).toLocaleString()}</span>}
												</div>
											</CardContent>
										</Card>
									</Link>
								))}
							</div>
						)}
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
				<CardTitle>No markets configured yet</CardTitle>
				<CardDescription>
					An operator can add the first market via the admin panel. Once enabled, the collector starts producing data within five minutes and the scorer follows hourly.
				</CardDescription>
			</CardHeader>
		</Card>
	)
}

MarketList.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
