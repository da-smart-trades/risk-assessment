// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link, usePage } from "@inertiajs/react"
import { RocketIcon, ShieldCheckIcon, ZapIcon } from "lucide-react"
import { CertoraBanner } from "@/components/logo"
import { RetroGrid } from "@/components/ui/retro-grid"
import { GuestLayout } from "@/layouts/guest-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"
import UserLoginForm from "./auth/partials/user-login-form"

export default function Landing() {
	const { mustVerifyEmail } = usePage<PagePropsFor<"landing">>().props

	return (
		<>
			<Head title="Certora Blockchain Risk Assessment" />

			{/* Hero Panel - Left Side */}
			<div className="relative hidden h-full flex-col bg-muted p-10 text-foreground lg:flex dark:border-r">
				<RetroGrid />
				<Link href={route("home")} className="relative z-20">
					<CertoraBanner className="h-8" />
				</Link>

				<div className="relative z-20 mt-auto space-y-6">
					<div className="space-y-2">
						<h2 className="text-3xl font-bold tracking-tight">
							Assess risk.
							<br />
							Secure with confidence.
						</h2>
						<p className="text-lg text-muted-foreground">Comprehensive blockchain risk assessment for DeFi protocols and smart contracts.</p>
					</div>

					<div className="grid gap-3">
						<div className="flex items-center gap-3">
							<div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
								<ZapIcon className="h-4 w-4 text-primary" />
							</div>
							<span className="text-sm">Real-time on-chain risk monitoring</span>
						</div>
						<div className="flex items-center gap-3">
							<div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
								<ShieldCheckIcon className="h-4 w-4 text-primary" />
							</div>
							<span className="text-sm">Smart contract vulnerability analysis</span>
						</div>
						<div className="flex items-center gap-3">
							<div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
								<RocketIcon className="h-4 w-4 text-primary" />
							</div>
							<span className="text-sm">DeFi protocol risk scoring</span>
						</div>
					</div>
				</div>
			</div>

			{/* Login Form - Right Side */}
			<div className="flex flex-col justify-center px-4 py-8 sm:px-6 lg:px-8">
				<div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-87.5">
					<div className="flex flex-col space-y-2 text-center">
						<h1 className="font-semibold text-2xl tracking-tight">Welcome to Certora Risk Assessment Platform</h1>
						<p className="text-muted-foreground text-sm">Enter your credentials to sign in to your account</p>
					</div>

					{mustVerifyEmail && (
						<div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-amber-900 text-sm">Email verification is required before you can sign in.</div>
					)}

					<UserLoginForm />

					<div className="text-center">
						<Link href={route("forgot-password")} className="text-sm text-muted-foreground underline-offset-4 hover:text-primary hover:underline">
							Forgot your password?
						</Link>
					</div>

					<p className="px-8 text-center text-muted-foreground text-sm">
						By continuing, you agree to our{" "}
						<Link href={route("terms-of-service")} className="underline underline-offset-4 hover:text-primary">
							Terms of Service
						</Link>{" "}
						and{" "}
						<Link href={route("privacy-policy")} className="underline underline-offset-4 hover:text-primary">
							Privacy Policy
						</Link>
						.
					</p>
				</div>
			</div>
		</>
	)
}

Landing.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
