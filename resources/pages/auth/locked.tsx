// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import { Lock } from "lucide-react"
import { AuthHeroPanel } from "@/components/auth-hero-panel"
import { Button } from "@/components/ui/button"
import { GuestLayout } from "@/layouts/guest-layout"

export default function Locked() {
	return (
		<>
			<Head title="Account locked" />
			<AuthHeroPanel description="Your account is temporarily locked." />

			<div className="flex flex-col justify-center px-4 py-8 sm:px-6 lg:px-8">
				<div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-87.5">
					<div className="flex flex-col items-center space-y-2 text-center">
						<Lock className="h-10 w-10 text-muted-foreground" />
						<h1 className="font-semibold text-2xl tracking-tight">Too many failed attempts</h1>
						<p className="text-muted-foreground text-sm">
							For your protection, we've temporarily blocked sign-ins from this network for this account. The lockout expires automatically; you can also unlock immediately using
							the link we just emailed you.
						</p>
					</div>

					<div className="space-y-3 rounded-md border bg-muted/50 p-4 text-sm">
						<p>
							<strong>If this was you:</strong> open the unlock email and click the link. Your account is unlocked right away and you can sign in.
						</p>
						<p>
							<strong>If this wasn't you:</strong> someone else is trying your password. Ignore the unlock email; the lockout will expire on its own and we recommend rotating your
							password through the forgot-password flow.
						</p>
						<p>
							<strong>Need help?</strong> Contact your team admin — they can force-unlock from the admin panel.
						</p>
					</div>

					<div className="flex justify-center gap-3">
						<Link href="/login">
							<Button variant="outline">Back to sign in</Button>
						</Link>
						<Link href="/forgot-password">
							<Button variant="default">Reset password</Button>
						</Link>
					</div>
				</div>
			</div>
		</>
	)
}

Locked.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
