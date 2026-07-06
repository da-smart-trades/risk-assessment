// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, useForm } from "@inertiajs/react"
import { KeyRound } from "lucide-react"
import { AuthHeroPanel } from "@/components/auth-hero-panel"
import { InputError } from "@/components/input-error"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { GuestLayout } from "@/layouts/guest-layout"

export default function Reauth() {
	const { data, setData, post, processing, errors } = useForm({ password: "" })

	const submit = (e: React.FormEvent) => {
		e.preventDefault()
		post("/auth/reauth/password")
	}

	return (
		<>
			<Head title="Confirm it's you" />
			<AuthHeroPanel description="Confirm your password to continue." />

			<div className="flex flex-col justify-center px-4 py-8 sm:px-6 lg:px-8">
				<div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-87.5">
					<div className="flex flex-col items-center space-y-2 text-center">
						<KeyRound className="h-10 w-10 text-muted-foreground" />
						<h1 className="font-semibold text-2xl tracking-tight">Confirm it's you</h1>
						<p className="text-muted-foreground text-sm">For security, please re-enter your password before continuing.</p>
					</div>

					<form onSubmit={submit} className="space-y-4">
						<div>
							<Label htmlFor="password">Password</Label>
							<Input id="password" type="password" value={data.password} onChange={(e) => setData("password", e.target.value)} autoFocus autoComplete="current-password" />
							<InputError message={errors.password} className="mt-2" />
						</div>
						<Button type="submit" className="w-full" disabled={processing}>
							Confirm
						</Button>
					</form>
				</div>
			</div>
		</>
	)
}

Reauth.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
