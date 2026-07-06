// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Link, usePage } from "@inertiajs/react"
import { Bell, ClipboardList, Coins, FileText, Home, Info, Layers, Network, ShieldCheck, Store, Users } from "lucide-react"
import type * as React from "react"
import { useEffect, useMemo, useState } from "react"
import { CertoraBanner, Logo } from "@/components/logo"
import { NavMain } from "@/components/nav-main"
import { TeamSwitcher } from "@/components/team-switcher"
import { Sidebar, SidebarContent, SidebarHeader, SidebarRail } from "@/components/ui/sidebar"
import { formatChainId } from "@/lib/chain-labels"
import { formatMarketLabel } from "@/lib/format-evidence"
import type { FullSharedProps } from "@/lib/generated/page-props"
import { isCurrentRoute, route } from "@/lib/generated/routes"
import { CHAIN_LOGOS, PROTOCOL_LOGOS, TOKEN_LOGOS } from "@/lib/logos"

/** One discovered market as returned by `/api/markets/alert-options`. */
interface DiscoveredMarket {
	protocol: string
	chainId: number
	marketIdHex: string
	label: string
}

/** Title-case a protocol slug for display: `aave-v3` → `Aave V3`. */
function formatProtocol(slug: string): string {
	return slug
		.split(/[-_\s]+/)
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(" ")
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
	const { auth } = usePage<FullSharedProps>().props

	// Discovered markets power the nested Markets nav (Markets → protocol →
	// markets). Loaded once client-side from the JSON endpoint rather than
	// shared on every request — the auth handler runs per-request and a
	// 70+ row query there would tax all traffic. The persistent app layout
	// keeps this sidebar mounted across Inertia visits, so it fetches once.
	const [markets, setMarkets] = useState<DiscoveredMarket[]>([])
	useEffect(() => {
		const controller = new AbortController()
		fetch(route("markets:alert_options"), {
			headers: { Accept: "application/json" },
			credentials: "same-origin",
			signal: controller.signal,
		})
			.then((res) => (res.ok ? res.json() : { items: [] }))
			.then((data: { items?: DiscoveredMarket[] }) => setMarkets(data.items ?? []))
			.catch(() => {
				/* network error / aborted — leave Markets as a plain link */
			})
		return () => controller.abort()
	}, [])

	// Group markets protocol → chain → markets, then build the three-level
	// submenu: "All Markets" link, then one expandable group per protocol,
	// each expanding into chain sub-groups, each listing its markets.
	const marketNav = useMemo(() => {
		if (markets.length === 0) {
			return undefined
		}
		// protocol → chainId → markets[]
		const byProtocol = new Map<string, Map<number, DiscoveredMarket[]>>()
		for (const m of markets) {
			let chainMap = byProtocol.get(m.protocol)
			if (!chainMap) {
				chainMap = new Map()
				byProtocol.set(m.protocol, chainMap)
			}
			const bucket = chainMap.get(m.chainId)
			if (bucket) {
				bucket.push(m)
			} else {
				chainMap.set(m.chainId, [m])
			}
		}
		const groups = Array.from(byProtocol.entries()).map(([protocol, chainMap]) => {
			const chains = Array.from(chainMap.entries())
			// Single chain: skip the chain level and list markets directly.
			if (chains.length === 1) {
				const [, rows] = chains[0]
				return {
					title: formatProtocol(protocol),
					items: rows.map((m) => ({
						title: formatMarketLabel(m.label),
						href: route("markets.show", { protocol: m.protocol, chain_id: m.chainId, market_id_hex: m.marketIdHex }),
					})),
				}
			}
			// Multiple chains: one sub-group per chain.
			return {
				title: formatProtocol(protocol),
				items: chains.map(([chainId, rows]) => ({
					title: formatChainId(chainId),
					items: rows.map((m) => ({
						title: formatMarketLabel(m.label),
						href: route("markets.show", { protocol: m.protocol, chain_id: m.chainId, market_id_hex: m.marketIdHex }),
					})),
				})),
			}
		})
		return [{ title: "All Markets", href: route("markets.list") }, ...groups]
	}, [markets])

	const navItems = useMemo(() => {
		const items = [
			{
				title: "Home",
				href: route("home"),
				icon: Home,
				isActive: isCurrentRoute("dashboard"),
			},
			{
				title: "Block Chains",
				href: route("chains.list"),
				icon: Network,
				isActive: isCurrentRoute("chains.*"),
				items: [
					{ title: "All Chains", href: route("chains.list") },
					{ title: "Arbitrum", href: route("chains.show", { chain_name: "ARBITRUM" }), logo: CHAIN_LOGOS.ARBITRUM },
					{ title: "Ethereum", href: route("chains.show", { chain_name: "ETHEREUM" }), logo: CHAIN_LOGOS.ETHEREUM },
					{ title: "Solana", href: route("chains.show", { chain_name: "SOLANA" }), logo: CHAIN_LOGOS.SOLANA },
					{ title: "Base", href: route("chains.show", { chain_name: "BASE" }), logo: CHAIN_LOGOS.BASE },
					{ title: "Ink", href: route("chains.show", { chain_name: "INK" }), logo: CHAIN_LOGOS.INK },
					{ title: "Unichain", href: route("chains.show", { chain_name: "UNICHAIN" }), logo: CHAIN_LOGOS.UNICHAIN },
					{ title: "Polygon", href: route("chains.show", { chain_name: "POLYGON" }), logo: CHAIN_LOGOS.POLYGON },
					{ title: "Avalanche C", href: route("chains.show", { chain_name: "AVALANCHE_C" }), logo: CHAIN_LOGOS.AVALANCHE_C },
					{ title: "Optimism", href: route("chains.show", { chain_name: "OPTIMISM" }), logo: CHAIN_LOGOS.OPTIMISM },
					{ title: "Canton", href: route("chains.show", { chain_name: "CANTON" }), logo: CHAIN_LOGOS.CANTON },
				],
			},
			{
				title: "Protocols",
				href: route("protocols.list"),
				icon: Layers,
				isActive: isCurrentRoute("protocols.*"),
				items: [
					{ title: "All Protocols", href: route("protocols.list") },
					{ title: "Aave v3", href: route("protocols.show", { protocol_name: "AAVE_V3" }), logo: PROTOCOL_LOGOS.AAVE_V3 },
					{ title: "Morpho v2", href: route("protocols.show", { protocol_name: "MORPHO_V2" }), logo: PROTOCOL_LOGOS.MORPHO_V2 },
					{ title: "Compound v3", href: route("protocols.show", { protocol_name: "COMPOUND_V3" }), logo: PROTOCOL_LOGOS.COMPOUND_V3 },
					{ title: "Drift v2", href: route("protocols.show", { protocol_name: "DRIFT_V2" }), logo: PROTOCOL_LOGOS.DRIFT_V2 },
				],
			},
			{
				title: "Tokens",
				href: route("tokens.list"),
				icon: Coins,
				isActive: isCurrentRoute("tokens.*"),
				items: [
					{ title: "All Tokens", href: route("tokens.list") },
					{ title: "Uniswap (UNI)", href: route("tokens.show", { token_name: "UNI" }), logo: TOKEN_LOGOS.UNI },
					{ title: "Aave (AAVE)", href: route("tokens.show", { token_name: "AAVE" }), logo: TOKEN_LOGOS.AAVE },
					{ title: "USDe", href: route("tokens.show", { token_name: "USDE" }), logo: TOKEN_LOGOS.USDE },
					{ title: "WETH", href: route("tokens.show", { token_name: "WETH" }), logo: TOKEN_LOGOS.WETH },
					{ title: "LINK", href: route("tokens.show", { token_name: "LINK" }), logo: TOKEN_LOGOS.LINK },
					{ title: "stETH", href: route("tokens.show", { token_name: "STETH" }), logo: TOKEN_LOGOS.STETH },
					{ title: "cbBTC", href: route("tokens.show", { token_name: "CBBTC" }), logo: TOKEN_LOGOS.CBBTC },
				],
			},
			{
				// Markets are discovered dynamically from collector snapshots.
				// `marketNav` (loaded client-side) expands Markets → protocol →
				// markets; until it loads, Markets is a plain link to the index.
				title: "Markets",
				href: route("markets.list"),
				icon: Store,
				isActive: isCurrentRoute("markets.*"),
				items: marketNav,
			},
			{
				title: "Security Reports",
				href: route("security_reports.list"),
				icon: FileText,
				isActive: isCurrentRoute("security_reports.*"),
			},
			{
				title: "Alerts",
				href: route("alerts"),
				icon: Bell,
				isActive: isCurrentRoute("alerts"),
			},
			{
				title: "Teams",
				href: route("teams.list"),
				icon: Users,
				isActive: isCurrentRoute("teams.*"),
				items: [
					{ title: "All Teams", href: route("teams.list") },
					{ title: "Create New", href: route("teams.add") },
				],
			},
			{
				title: "About",
				href: route("about"),
				icon: Info,
				isActive: isCurrentRoute("about"),
			},
		]

		if (auth?.user?.isOperatorEditor) {
			items.push({
				title: "Manual Metrics",
				href: route("manual_metrics.list"),
				icon: ClipboardList,
				isActive: isCurrentRoute("manual_metrics.*"),
				items: [
					{ title: "All Objects", href: route("manual_metrics.list") },
					{ title: "Chains", href: `${route("manual_metrics.list")}?entityType=chain` },
					{ title: "Protocols", href: `${route("manual_metrics.list")}?entityType=protocol` },
					{ title: "Tokens", href: `${route("manual_metrics.list")}?entityType=token` },
					{ title: "Markets", href: `${route("manual_metrics.list")}?entityType=market` },
					{ title: "Add new metric", href: route("manual_metrics.admin.create_page") },
				],
			})
		}

		if (auth?.user?.isSuperuser) {
			items.push({
				title: "Admin",
				href: "/admin",
				icon: ShieldCheck,
				isActive: isCurrentRoute("admin.*"),
				items: [
					{ title: "Market config", href: route("admin.market_config.list") },
					{ title: "Weighting profiles", href: route("admin.weighting_profiles.list") },
				],
			})
		} else if (auth?.user?.isAnyTeamEditor) {
			// Team admins/editors don't see the rest of Admin, but they
			// still need their own weighting profiles to land on the
			// PD card, so we surface a single direct entry instead.
			items.push({
				title: "Weighting profiles",
				href: route("admin.weighting_profiles.list"),
				icon: ShieldCheck,
				isActive: isCurrentRoute("admin.weighting_profiles.*"),
			})
		}

		return items
	}, [auth?.user?.isSuperuser, auth?.user?.isOperatorEditor, auth?.user?.isAnyTeamEditor, marketNav])

	return (
		<Sidebar collapsible="icon" {...props}>
			<SidebarHeader>
				<Link href={route("home")} className="flex items-center px-2 py-1.5 group-data-[collapsible=icon]:justify-center">
					<CertoraBanner className="h-6 group-data-[collapsible=icon]:hidden" />
					<Logo className="hidden group-data-[collapsible=icon]:block h-6 w-6" />
				</Link>
				<TeamSwitcher />
			</SidebarHeader>
			<SidebarContent>
				<NavMain items={navItems} />
			</SidebarContent>
			<SidebarRail />
		</Sidebar>
	)
}
