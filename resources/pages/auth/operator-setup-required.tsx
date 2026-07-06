// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { ServerCog } from "lucide-react"
import { AuthHeroPanel } from "@/components/auth-hero-panel"
import { Button } from "@/components/ui/button"
import { GuestLayout } from "@/layouts/guest-layout"

export default function OperatorSetupRequired() {
	return (
		<>
			<Head title="Operator setup required" />
			<AuthHeroPanel description="Passkey required for operator sign-in." />

			<div className="flex flex-col justify-center px-4 py-8 sm:px-6 lg:px-8">
				<div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-87.5">
					<div className="flex flex-col items-center space-y-2 text-center">
						<ServerCog className="h-10 w-10 text-muted-foreground" />
						<h1 className="font-semibold text-2xl tracking-tight">Operator setup required</h1>
						<p className="text-muted-foreground text-sm">
							You need an enrolled passkey before signing in. Add one from the account-recovery flow or contact your Certora-side admin.
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

OperatorSetupRequired.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
