// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head } from "@inertiajs/react"
import axios from "axios"
import { Check, Star } from "lucide-react"
import type React from "react"
import { useMemo, useState } from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { MarketMetricsPanel } from "@/components/market-metrics-panel"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { AppLayout } from "@/layouts/app-layout"
import { formatChainId } from "@/lib/chain-labels"
import { formatMarketLabel } from "@/lib/format-evidence"
import type { Favorite } from "@/lib/generated/api/types.gen"
import type { PagePropsFor } from "@/lib/generated/page-props"

type DashboardOption = PagePropsFor<"market/show">["dashboards"][number]

export default function MarketShow({
	market,
	pd,
	trend,
	scoring,
	anchors,
	modifiers,
	metricsCapturedAt,
	assuranceMetrics,
	appliedProfile,
	dashboards,
}: PagePropsFor<"market/show">) {
	const displayLabel = formatMarketLabel(market.label)
	return (
		<>
			<Head title={displayLabel} />
			<Header title={displayLabel} subtitle={`${market.protocol} · ${formatChainId(market.chainId)} · ${market.marketIdHex.slice(0, 10)}…`} />
			<Container>
				<MarketMetricsPanel
					pd={pd}
					trend={trend}
					scoring={scoring}
					anchors={anchors}
					modifiers={modifiers}
					metricsCapturedAt={metricsCapturedAt}
					assuranceMetrics={assuranceMetrics}
					appliedProfile={appliedProfile}
					favoriteSlot={<FavoriteButton marketConfigId={market.id} chainId={market.chainId} marketIdHex={market.marketIdHex} label={market.label} dashboards={dashboards} />}
				/>
			</Container>
		</>
	)
}

interface FavoriteButtonProps {
	marketConfigId: string
	chainId: number
	marketIdHex: string
	label: string
	dashboards: DashboardOption[]
}

function FavoriteButton({ marketConfigId, chainId, marketIdHex, label, dashboards: initial }: FavoriteButtonProps) {
	const [dashboards, setDashboards] = useState(initial)
	const [pending, setPending] = useState<string | null>(null)

	const anyFavorited = useMemo(() => dashboards.some((d) => d.containsMarket), [dashboards])
	const hasDashboards = dashboards.length > 0

	const setOne = (id: string, patch: Partial<DashboardOption>) => setDashboards((prev) => prev.map((d) => (d.id === id ? { ...d, ...patch } : d)))

	const toggle = async (target: DashboardOption) => {
		if (pending) return
		setPending(target.id)
		try {
			// These are JSON API endpoints (they return a Favorite, not an
			// Inertia response), so hit them with axios — Inertia's router
			// would discard the non-Inertia response and never run onSuccess.
			if (target.containsMarket && target.favoriteId) {
				await axios.delete(`/api/dashboards/${target.id}/favorites/${target.favoriteId}`)
				setOne(target.id, { containsMarket: false, favoriteId: null })
			} else {
				const res = await axios.post<Favorite>(`/api/dashboards/${target.id}/favorites/market`, {
					marketConfigId: marketConfigId,
					favoriteChainId: chainId,
					favoriteMarketIdHex: marketIdHex,
					favoriteLabel: label,
				})
				setOne(target.id, { containsMarket: true, favoriteId: res.data.id })
			}
		} finally {
			setPending(null)
		}
	}

	// Single-dashboard shortcut: avoid the dropdown entirely.
	if (dashboards.length === 1) {
		const only = dashboards[0]
		const lit = only.containsMarket
		return (
			<Button
				size="icon"
				variant={lit ? "default" : "outline"}
				onClick={() => toggle(only)}
				disabled={pending !== null}
				aria-pressed={lit}
				aria-label={lit ? `Remove from ${only.name}` : `Add to ${only.name}`}
				title={lit ? `Remove from ${only.name}` : `Add to ${only.name}`}
			>
				<Star className={`h-4 w-4 ${lit ? "fill-current" : ""}`} aria-hidden="true" />
			</Button>
		)
	}

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					size="icon"
					variant={anyFavorited ? "default" : "outline"}
					disabled={!hasDashboards}
					aria-pressed={anyFavorited}
					aria-label={anyFavorited ? "Manage favorite dashboards" : "Add to a dashboard"}
					title={hasDashboards ? "Pin to a dashboard" : "Create a dashboard first"}
				>
					<Star className={`h-4 w-4 ${anyFavorited ? "fill-current" : ""}`} aria-hidden="true" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="end">
				<DropdownMenuLabel>Pin to dashboard</DropdownMenuLabel>
				<DropdownMenuSeparator />
				{dashboards.map((d) => (
					<DropdownMenuItem key={d.id} onSelect={() => toggle(d)} disabled={pending === d.id}>
						<span className="flex w-full items-center justify-between gap-3">
							<span className="truncate">
								{d.name}
								{d.isDefault ? <span className="ml-1 text-muted-foreground">· default</span> : null}
							</span>
							{d.containsMarket ? <Check className="h-4 w-4" aria-hidden="true" /> : null}
						</span>
					</DropdownMenuItem>
				))}
			</DropdownMenuContent>
		</DropdownMenu>
	)
}

MarketShow.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
