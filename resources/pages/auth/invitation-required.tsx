// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { MailQuestion } from "lucide-react"
import { AuthHeroPanel } from "@/components/auth-hero-panel"
import { Button } from "@/components/ui/button"
import { GuestLayout } from "@/layouts/guest-layout"

export default function InvitationRequired() {
	return (
		<>
			<Head title="Invitation required" />
			<AuthHeroPanel description="Ask your team admin to invite you." />

			<div className="flex flex-col justify-center px-4 py-8 sm:px-6 lg:px-8">
				<div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-87.5">
					<div className="flex flex-col items-center space-y-2 text-center">
						<MailQuestion className="h-10 w-10 text-muted-foreground" />
						<h1 className="font-semibold text-2xl tracking-tight">Invitation required</h1>
						<p className="text-muted-foreground text-sm">We don't have an account ready for your sign-in. Ask your team admin to send you an invitation.</p>
					</div>

					<Link href="/login">
						<Button className="w-full">Back to sign in</Button>
					</Link>
				</div>
			</div>
		</>
	)
}

InvitationRequired.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
