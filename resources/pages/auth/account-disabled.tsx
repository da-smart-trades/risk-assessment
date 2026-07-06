// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { UserX } from "lucide-react"
import { AuthHeroPanel } from "@/components/auth-hero-panel"
import { Button } from "@/components/ui/button"
import { GuestLayout } from "@/layouts/guest-layout"

export default function AccountDisabled() {
	return (
		<>
			<Head title="Account disabled" />
			<AuthHeroPanel description="This account is not active." />

			<div className="flex flex-col justify-center px-4 py-8 sm:px-6 lg:px-8">
				<div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-87.5">
					<div className="flex flex-col items-center space-y-2 text-center">
						<UserX className="h-10 w-10 text-muted-foreground" />
						<h1 className="font-semibold text-2xl tracking-tight">Account disabled</h1>
						<p className="text-muted-foreground text-sm">This account is not currently active. Contact your team admin or our support team to restore access.</p>
					</div>

					<Link href="/login">
						<Button className="w-full">Back to sign in</Button>
					</Link>
				</div>
			</div>
		</>
	)
}

AccountDisabled.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
