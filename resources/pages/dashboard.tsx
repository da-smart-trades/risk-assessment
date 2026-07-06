// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { Coins, HelpCircle, Layers, Link2, Star, Store } from "lucide-react"
import type React from "react"
import type { ComponentType, SVGProps } from "react"
import { Container } from "@/components/container"
import { DashboardSwitcher } from "@/components/dashboard-switcher"
import { Header } from "@/components/header"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { AppLayout } from "@/layouts/app-layout"
import type { DashboardSummary, ResolvedFavorite } from "@/lib/generated/api/types.gen"
import type { PagePropsFor } from "@/lib/generated/page-props"

type LucideIcon = ComponentType<SVGProps<SVGSVGElement>>

const CARD_KIND_ICON: Record<string, { icon: LucideIcon; label: string }> = {
	chain: { icon: Link2, label: "Chain" },
	protocol: { icon: Layers, label: "Protocol" },
	market: { icon: Store, label: "Market" },
	token: { icon: Coins, label: "Token" },
}

// Home favorites are grouped into these sections by their `cardKind`. Order is
// fixed (protocols, chains, tokens, markets) regardless of pin order; the
// trailing `null` bucket catches favorites with no kind (e.g. a manual metric
// without a protocol) so nothing pinned is ever dropped.
const SECTIONS: { kind: string | null; title: string; icon: LucideIcon }[] = [
	{ kind: "protocol", title: "Protocols", icon: Layers },
	{ kind: "chain", title: "Chains", icon: Link2 },
	{ kind: "token", title: "Tokens", icon: Coins },
	{ kind: "market", title: "Markets", icon: Store },
	{ kind: null, title: "Other", icon: Star },
]

export default function Dashboard(props: PagePropsFor<"dashboard">) {
	const current = props.current as DashboardSummary
	const dashboards = (props.dashboards as DashboardSummary[] | undefined) ?? []
	const items = (props.favorites as ResolvedFavorite[] | undefined) ?? []
	const canEdit = Boolean(props.canEdit)

	return (
		<>
			<Head title={current ? current.name : "Dashboard"} />
			<Header title="Home">{current && <DashboardSwitcher current={current} dashboards={dashboards} canEdit={canEdit} />}</Header>
			<Container>
				{!canEdit && current && (
					<div className="mb-4 flex items-center gap-2 text-sm text-muted-foreground">
						<Badge variant="secondary">Shared</Badge>
						<span>This home page is shared by {current.ownerName ?? "a teammate"}. It’s read-only — copy a favorite to one of your own pages to customize it.</span>
					</div>
				)}
				{items.length === 0 ? <EmptyState canEdit={canEdit} /> : <FavoriteSections favorites={items} />}
			</Container>
		</>
	)
}

function FavoriteSections({ favorites }: { favorites: ResolvedFavorite[] }) {
	// Bucket favorites by kind once, preserving each kind's pin order. A kind
	// (e.g. "protocol") that isn't one of the named sections falls into the
	// trailing `null` ("Other") bucket so it still renders somewhere.
	const known = new Set(SECTIONS.map((s) => s.kind).filter((k): k is string => k != null))
	const byKind = new Map<string | null, ResolvedFavorite[]>()
	for (const fav of favorites) {
		const key = fav.cardKind != null && known.has(fav.cardKind) ? fav.cardKind : null
		const bucket = byKind.get(key)
		if (bucket) {
			bucket.push(fav)
		} else {
			byKind.set(key, [fav])
		}
	}

	return (
		<div className="space-y-8">
			{SECTIONS.map((section) => {
				const sectionFavorites = byKind.get(section.kind)
				if (!sectionFavorites || sectionFavorites.length === 0) {
					return null
				}
				const SectionIcon = section.icon
				return (
					<section key={section.title}>
						<h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
							<SectionIcon className="h-4 w-4" />
							{section.title}
						</h2>
						<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
							{sectionFavorites.map((fav) => (
								<FavoriteCard key={fav.id} fav={fav} />
							))}
						</div>
					</section>
				)
			})}
		</div>
	)
}

function FavoriteCard({ fav }: { fav: ResolvedFavorite }) {
	const hasSecondary = fav.secondaryValue != null || fav.secondaryLabel != null
	const kindEntry = fav.cardKind ? CARD_KIND_ICON[fav.cardKind] : undefined
	const KindIcon = kindEntry?.icon
	return (
		<Link href={fav.href} className="block transition hover:opacity-90">
			<Card className="relative">
				{fav.description && (
					<Tooltip>
						<TooltipTrigger asChild>
							<button
								type="button"
								aria-label="About this metric"
								onClick={(e) => e.preventDefault()}
								className="absolute right-3 top-3 inline-flex h-5 w-5 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground"
							>
								<HelpCircle className="h-4 w-4" />
							</button>
						</TooltipTrigger>
						<TooltipContent className="max-w-80 text-xs leading-relaxed">{fav.description}</TooltipContent>
					</Tooltip>
				)}
				<CardHeader className="pb-2">
					<CardTitle className="flex items-center gap-2 pr-7 text-sm font-medium text-muted-foreground">
						{KindIcon && (
							<Tooltip>
								<TooltipTrigger asChild>
									<span role="img" aria-label={kindEntry.label} className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
										<KindIcon className="h-3.5 w-3.5" />
									</span>
								</TooltipTrigger>
								<TooltipContent className="text-xs">{kindEntry.label}</TooltipContent>
							</Tooltip>
						)}
						<span>{fav.label}</span>
					</CardTitle>
				</CardHeader>
				<CardContent className="space-y-3">
					<div>
						{fav.primaryLabel && <p className="mb-0.5 text-xs text-muted-foreground">{fav.primaryLabel}</p>}
						<p className="text-brand-gradient text-3xl font-bold tabular-nums tracking-tight">{fav.value ?? "—"}</p>
					</div>
					{hasSecondary && (
						<div>
							{fav.secondaryLabel && <p className="mb-0.5 text-xs text-muted-foreground">{fav.secondaryLabel}</p>}
							<p className="text-lg font-semibold tabular-nums tracking-tight text-muted-foreground">{fav.secondaryValue ?? "—"}</p>
						</div>
					)}
				</CardContent>
			</Card>
		</Link>
	)
}

function EmptyState({ canEdit }: { canEdit: boolean }) {
	return (
		<div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted/30 px-8 py-16 text-center">
			<Star className="h-10 w-10 text-muted-foreground/60" />
			<h2 className="mt-4 text-lg font-semibold">No favorites yet</h2>
			<p className="mt-2 max-w-md text-sm text-muted-foreground">
				{canEdit
					? "On a chain page or a PROTOCOL_SCORE manual metric, click the ★ and choose this home page to pin a metric here."
					: "This shared home page doesn’t have any favorites yet."}
			</p>
		</div>
	)
}

Dashboard.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
