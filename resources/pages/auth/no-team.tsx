// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { Users } from "lucide-react"
import { AuthHeroPanel } from "@/components/auth-hero-panel"
import { Button } from "@/components/ui/button"
import { GuestLayout } from "@/layouts/guest-layout"

export default function NoTeam() {
	return (
		<>
			<Head title="No team membership" />
			<AuthHeroPanel description="You're not on any team yet." />

			<div className="flex flex-col justify-center px-4 py-8 sm:px-6 lg:px-8">
				<div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-87.5">
					<div className="flex flex-col items-center space-y-2 text-center">
						<Users className="h-10 w-10 text-muted-foreground" />
						<h1 className="font-semibold text-2xl tracking-tight">No team membership</h1>
						<p className="text-muted-foreground text-sm">
							Your account isn't on any team yet, so there's nothing for you to view. Ask your team admin to add you, or contact our support team if you think this is wrong.
						</p>
					</div>

					<div className="flex justify-center gap-3">
						<Link href="/logout" method="post" as="button" className="block">
							<Button variant="outline">Sign out</Button>
						</Link>
					</div>
				</div>
			</div>
		</>
	)
}

NoTeam.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
