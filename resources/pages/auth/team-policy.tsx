// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { Building2 } from "lucide-react"
import { AuthHeroPanel } from "@/components/auth-hero-panel"
import { Button } from "@/components/ui/button"
import { GuestLayout } from "@/layouts/guest-layout"

export default function TeamPolicy() {
	return (
		<>
			<Head title="Team sign-in policy" />
			<AuthHeroPanel description="Your team enforces a sign-in policy." />

			<div className="flex flex-col justify-center px-4 py-8 sm:px-6 lg:px-8">
				<div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-87.5">
					<div className="flex flex-col items-center space-y-2 text-center">
						<Building2 className="h-10 w-10 text-muted-foreground" />
						<h1 className="font-semibold text-2xl tracking-tight">Team sign-in policy</h1>
						<p className="text-muted-foreground text-sm">Your team requires a specific sign-in method. Sign in again using the method your team admin has enforced.</p>
					</div>

					<Link href="/login">
						<Button className="w-full">Back to sign in</Button>
					</Link>
				</div>
			</div>
		</>
	)
}

TeamPolicy.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
