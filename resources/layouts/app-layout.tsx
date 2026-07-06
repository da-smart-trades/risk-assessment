// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { usePage } from "@inertiajs/react"
import { type PropsWithChildren, useMemo } from "react"
import { AppSidebar } from "@/components/app-sidebar"
import { FavoritesProvider } from "@/components/favorites-provider"
import { HeaderUserMenu } from "@/components/header-user-menu"
import { ThemeToggle } from "@/components/theme-toggle"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { Toaster } from "@/components/ui/toaster"
import { useFlashMessages } from "@/hooks/use-flash-messages"
import type { FullSharedProps } from "@/lib/generated/page-props"
import { cn } from "@/lib/utils"

type AppLayoutProps = PropsWithChildren<{ mainClassName?: string }>

export function AppLayout({ children, mainClassName }: AppLayoutProps) {
	useFlashMessages()

	const { url } = usePage<FullSharedProps>()

	const header = useMemo(() => {
		if (url === "/" || url.startsWith("/dashboard") || url.startsWith("/home")) {
			return { eyebrow: "Overview", title: "Home" }
		}
		if (url.startsWith("/teams/new") || url.startsWith("/teams/create")) {
			return { eyebrow: "Workspace", title: "Create Team" }
		}
		if (url.startsWith("/teams")) {
			return { eyebrow: "Workspace", title: "Teams" }
		}
		if (url.startsWith("/chains")) {
			return { eyebrow: "Workspace", title: "Block Chains" }
		}
		if (url.startsWith("/protocols")) {
			return { eyebrow: "Workspace", title: "Protocols" }
		}
		if (url.startsWith("/markets")) {
			return { eyebrow: "Workspace", title: "Markets" }
		}
		if (url.startsWith("/admin")) {
			return { eyebrow: "Operations", title: "Admin" }
		}
		if (url.startsWith("/profile")) {
			return { eyebrow: "Account", title: "Profile" }
		}
		if (url.startsWith("/about")) {
			return { eyebrow: "Info", title: "About" }
		}
		return { eyebrow: "Workspace", title: "Dashboard" }
	}, [url])

	return (
		<FavoritesProvider>
			<SidebarProvider>
				<Toaster />
				<AppSidebar />
				<SidebarInset>
					<header className="flex h-16 shrink-0 items-center gap-2 border-b border-border/60 bg-background/80 backdrop-blur sticky top-0 z-10">
						<div className="flex w-full items-center gap-4 px-4">
							<SidebarTrigger className="-ml-1" />
							<Separator orientation="vertical" className="mr-2 h-4" />
							<div>
								<p className="text-[0.65rem] font-semibold uppercase tracking-[0.24em] text-muted-foreground">{header.eyebrow}</p>
								<p className="text-lg font-semibold text-brand-gradient">{header.title}</p>
							</div>
							<div className="ml-auto flex items-center gap-2">
								<HeaderUserMenu />
							</div>
						</div>
					</header>
					<main className={cn("flex-1 pb-10", mainClassName)}>{children}</main>
				</SidebarInset>
				<div className="fixed bottom-4 right-4 z-40 rounded-full border border-border/60 bg-background/80 p-1 shadow-lg backdrop-blur">
					<ThemeToggle />
				</div>
			</SidebarProvider>
		</FavoritesProvider>
	)
}
