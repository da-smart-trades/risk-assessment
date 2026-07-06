// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Link } from "@inertiajs/react"
import { CertoraBanner } from "@/components/logo"
import { RetroGrid } from "@/components/ui/retro-grid"
import { route } from "@/lib/generated/routes"

interface AuthHeroPanelProps {
	description?: string
	showTestimonial?: boolean
}

export function AuthHeroPanel({
	description = "Blockchain risk assessment platform for DeFi protocols, smart contracts, and on-chain assets.",
	showTestimonial = true,
}: AuthHeroPanelProps) {
	return (
		<div className="relative hidden h-full flex-col bg-muted p-10 text-foreground lg:flex dark:border-r">
			<RetroGrid />
			<Link href={route("home")} className="relative z-20">
				<CertoraBanner className="h-8" />
			</Link>

			<div className="relative z-20 mt-auto">
				{showTestimonial ? (
					<div className="space-y-4">
						<p className="text-lg font-medium leading-relaxed">{description}</p>
						<div className="flex items-center gap-4">
							<div className="flex -space-x-2">
								<div className="h-8 w-8 rounded-full bg-primary/20 ring-2 ring-background" />
								<div className="h-8 w-8 rounded-full bg-primary/30 ring-2 ring-background" />
								<div className="h-8 w-8 rounded-full bg-primary/40 ring-2 ring-background" />
							</div>
							<div className="text-sm text-muted-foreground">
								<span className="font-medium text-foreground">Powered by</span> Certora verification technology
							</div>
						</div>
					</div>
				) : (
					<p className="text-lg font-medium leading-relaxed text-muted-foreground">{description}</p>
				)}
			</div>
		</div>
	)
}
