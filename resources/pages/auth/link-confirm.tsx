// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

/**
 * Password → OIDC link-confirm page.
 *
 * Shown after an OIDC sign-in matches a password-protected user (no
 * prior OAuth link). The user confirms password ownership; on success
 * the new OIDC identity is linked and `hashed_password` is cleared.
 */

import { Head, useForm, usePage } from "@inertiajs/react"
import { useEffect } from "react"
import { AuthHeroPanel } from "@/components/auth-hero-panel"
import { Icons } from "@/components/icons"
import { InputError } from "@/components/input-error"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { GuestLayout } from "@/layouts/guest-layout"
import { route } from "@/lib/generated/routes"

interface LinkConfirmProps {
	email: string
	provider: string
	providerLabel: string
}

export default function LinkConfirm() {
	const page = usePage<LinkConfirmProps>()
	const { email, providerLabel } = page.props
	const { data, setData, post, processing, errors, reset } = useForm({
		password: "",
	})

	useEffect(() => {
		return () => {
			reset("password")
		}
	}, [reset])

	const submit = (e: React.FormEvent) => {
		e.preventDefault()
		post(route("link-confirm.submit"))
	}

	return (
		<>
			<Head />

			<AuthHeroPanel description="Link your sign-in provider with your existing account." />

			<div className="flex flex-col justify-center px-4 py-8 sm:px-6 lg:px-8">
				<div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-87.5">
					<div className="flex flex-col space-y-2 text-center">
						<h1 className="flex items-center justify-center gap-2 font-semibold text-2xl tracking-tight">
							<Icons.lock className="h-5 w-5" />
							Link {providerLabel}
						</h1>
						<p className="text-muted-foreground text-sm">
							We see an existing account for <span className="font-medium">{email}</span>. Sign in with your password to link your {providerLabel} account. After linking, you'll
							sign in with {providerLabel} only.
						</p>
					</div>

					<form onSubmit={submit} className="space-y-4">
						<div>
							<Label htmlFor="password">Password</Label>
							<Input
								id="password"
								type="password"
								name="password"
								value={data.password}
								className="mt-1"
								autoFocus
								autoComplete="current-password"
								onChange={(e) => setData("password", e.target.value)}
							/>
							<InputError message={errors.password} className="mt-2" />
						</div>

						<Button type="submit" className="w-full" disabled={processing}>
							{processing ? <Icons.spinner className="mr-2 h-4 w-4" /> : null}
							Confirm and Link
						</Button>
					</form>
				</div>
			</div>
		</>
	)
}

LinkConfirm.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
