// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { ShieldAlert } from "lucide-react"
import { AuthHeroPanel } from "@/components/auth-hero-panel"
import { Button } from "@/components/ui/button"
import { GuestLayout } from "@/layouts/guest-layout"

export default function WrongProvider() {
	return (
		<>
			<Head title="Wrong sign-in method" />
			<AuthHeroPanel description="Try a different sign-in method." />

			<div className="flex flex-col justify-center px-4 py-8 sm:px-6 lg:px-8">
				<div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-87.5">
					<div className="flex flex-col items-center space-y-2 text-center">
						<ShieldAlert className="h-10 w-10 text-muted-foreground" />
						<h1 className="font-semibold text-2xl tracking-tight">Different sign-in method required</h1>
						<p className="text-muted-foreground text-sm">
							Your account is linked to a single-sign-on provider that doesn't match the one you just used. Sign in with the provider linked to your account.
						</p>
					</div>

					<Link href="/login">
						<Button className="w-full">Back to sign in</Button>
					</Link>
				</div>
			</div>
		</>
	)
}

WrongProvider.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
