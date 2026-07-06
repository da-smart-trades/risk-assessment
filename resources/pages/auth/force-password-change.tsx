// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, useForm } from "@inertiajs/react"
import type React from "react"
import { useEffect } from "react"
import { AuthHeroPanel } from "@/components/auth-hero-panel"
import { InputError } from "@/components/input-error"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { GuestLayout } from "@/layouts/guest-layout"
import { route } from "@/lib/generated/routes"

export default function ForcePasswordChange() {
	const { data, setData, post, processing, errors, reset } = useForm({
		password: "",
		confirm_password: "",
	})

	useEffect(() => {
		return () => reset("password", "confirm_password")
	}, [reset])

	const submit = (e: React.FormEvent) => {
		e.preventDefault()
		post(route("auth.force-password-change.submit"))
	}

	return (
		<>
			<Head title="Set a new password" />
			<AuthHeroPanel description="For security, this account must set a new password before continuing." />

			<div className="mx-auto w-full max-w-sm space-y-6">
				<div className="space-y-2 text-center">
					<h1 className="font-semibold text-2xl">Set a new password</h1>
					<p className="text-muted-foreground text-sm">This account uses password sign-in and must rotate its initial password.</p>
				</div>

				<form onSubmit={submit} className="space-y-4">
					<div>
						<Label htmlFor="password">New password</Label>
						<Input
							id="password"
							type="password"
							value={data.password}
							onChange={(e) => setData("password", e.target.value)}
							autoComplete="new-password"
							autoFocus
							className="mt-1"
						/>
						<InputError message={errors.password} className="mt-2" />
						<p className="mt-1 text-muted-foreground text-xs">At least 12 characters.</p>
					</div>

					<div>
						<Label htmlFor="confirm_password">Confirm new password</Label>
						<Input
							id="confirm_password"
							type="password"
							value={data.confirm_password}
							onChange={(e) => setData("confirm_password", e.target.value)}
							autoComplete="new-password"
							className="mt-1"
						/>
						<InputError message={errors.confirm_password} className="mt-2" />
					</div>

					<Button type="submit" className="w-full" disabled={processing}>
						Update password
					</Button>
				</form>
			</div>
		</>
	)
}

ForcePasswordChange.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
