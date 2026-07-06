// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link, useForm } from "@inertiajs/react"
import { AlertCircle, CheckCircle, KeyRound, LogIn, ShieldCheck, Users, XCircle } from "lucide-react"
import type React from "react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { GuestLayout } from "@/layouts/guest-layout"
import type { InvitationAcceptPage, OidcProviderOption } from "@/lib/generated/api/types.gen"
import { route } from "@/lib/generated/routes"

type Props = InvitationAcceptPage

export default function AcceptInvitation(props: Props) {
	const { invitation, isValid, errorMessage, isAuthenticated, loginUrl, isActivation, allowPassword, inviteeEmail, setPasswordUrl, oidcOptions } = props
	const acceptForm = useForm({})
	const declineForm = useForm({})

	// Get token from URL
	const token = typeof window !== "undefined" ? window.location.pathname.split("/")[2] : ""

	const handleAccept = () => {
		acceptForm.post(route("invitation.accept", { token }), {
			preserveScroll: true,
		})
	}

	const handleDecline = () => {
		declineForm.post(route("invitation.decline", { token }), {
			preserveScroll: true,
		})
	}

	// Invalid invitation (expired, already accepted, or not found)
	if (!isValid) {
		return (
			<>
				<Head title="Invalid Invitation" />
				<Card className="w-full max-w-md">
					<CardHeader className="text-center">
						<div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-destructive/10">
							<XCircle className="h-8 w-8 text-destructive" />
						</div>
						<CardTitle>Invitation Not Valid</CardTitle>
						<CardDescription>This invitation cannot be accepted.</CardDescription>
					</CardHeader>
					<CardContent>
						<Alert variant="destructive">
							<AlertCircle className="h-4 w-4" />
							<AlertTitle>Error</AlertTitle>
							<AlertDescription>{errorMessage}</AlertDescription>
						</Alert>
					</CardContent>
					<CardFooter className="justify-center gap-2">
						{isAuthenticated ? (
							<Button variant="outline" asChild>
								<a href="/dashboard">Go to Dashboard</a>
							</Button>
						) : (
							<Button variant="outline" asChild>
								<Link href={loginUrl || "/login/"}>
									<LogIn className="mr-2 h-4 w-4" />
									Log In
								</Link>
							</Button>
						)}
					</CardFooter>
				</Card>
			</>
		)
	}

	// First-time activation: invitee picks an OIDC provider or sets a password.
	if (isActivation) {
		return (
			<ActivationCard
				invitation={invitation}
				inviteeEmail={inviteeEmail ?? null}
				allowPassword={Boolean(allowPassword)}
				setPasswordUrl={setPasswordUrl ?? null}
				oidcOptions={oidcOptions ?? []}
			/>
		)
	}

	// Unauthenticated user - show invitation details and prompt to login/signup
	if (!isAuthenticated) {
		return (
			<>
				<Head title={`Join ${invitation.teamName}`} />
				<Card className="w-full max-w-md">
					<CardHeader className="text-center">
						<div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
							<Users className="h-8 w-8 text-primary" />
						</div>
						<CardTitle>You're Invited!</CardTitle>
						<CardDescription>
							<strong>{invitation.inviterName}</strong> has invited you to join <strong>{invitation.teamName}</strong>
						</CardDescription>
					</CardHeader>
					<CardContent className="space-y-4">
						<div className="rounded-lg bg-muted p-4">
							<div className="space-y-2 text-sm">
								<div className="flex justify-between">
									<span className="text-muted-foreground">Team</span>
									<span className="font-medium">{invitation.teamName}</span>
								</div>
								<div className="flex justify-between">
									<span className="text-muted-foreground">Role</span>
									<span className="font-medium capitalize">{invitation.role}</span>
								</div>
								<div className="flex justify-between">
									<span className="text-muted-foreground">Invited by</span>
									<span className="font-medium">{invitation.inviterEmail}</span>
								</div>
							</div>
						</div>
						<Alert>
							<AlertCircle className="h-4 w-4" />
							<AlertTitle>Sign in required</AlertTitle>
							<AlertDescription>Log in to accept this invitation.</AlertDescription>
						</Alert>
					</CardContent>
					<CardFooter>
						<Button variant="outline" className="flex-1" asChild>
							<Link href={loginUrl || "/login/"}>
								<LogIn className="mr-2 h-4 w-4" />
								Log In
							</Link>
						</Button>
					</CardFooter>
				</Card>
			</>
		)
	}

	// Authenticated and correct user - show accept/decline
	return (
		<>
			<Head title={`Join ${invitation.teamName}`} />
			<Card className="w-full max-w-md">
				<CardHeader className="text-center">
					<div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
						<Users className="h-8 w-8 text-primary" />
					</div>
					<CardTitle>You're Invited!</CardTitle>
					<CardDescription>
						<strong>{invitation.inviterName}</strong> has invited you to join <strong>{invitation.teamName}</strong>
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					<div className="rounded-lg bg-muted p-4">
						<div className="space-y-2 text-sm">
							<div className="flex justify-between">
								<span className="text-muted-foreground">Team</span>
								<span className="font-medium">{invitation.teamName}</span>
							</div>
							<div className="flex justify-between">
								<span className="text-muted-foreground">Role</span>
								<span className="font-medium capitalize">{invitation.role}</span>
							</div>
							<div className="flex justify-between">
								<span className="text-muted-foreground">Invited by</span>
								<span className="font-medium">{invitation.inviterEmail}</span>
							</div>
						</div>
					</div>
				</CardContent>
				<CardFooter className="flex gap-3">
					<Button variant="outline" className="flex-1" onClick={handleDecline} disabled={declineForm.processing}>
						Decline
					</Button>
					<Button className="flex-1" onClick={handleAccept} disabled={acceptForm.processing}>
						<CheckCircle className="mr-2 h-4 w-4" />
						Accept Invitation
					</Button>
				</CardFooter>
			</Card>
		</>
	)
}

interface ActivationCardProps {
	invitation: InvitationAcceptPage["invitation"]
	inviteeEmail: string | null
	allowPassword: boolean
	setPasswordUrl: string | null
	oidcOptions: OidcProviderOption[]
}

function ActivationCard({ invitation, inviteeEmail, allowPassword, setPasswordUrl, oidcOptions }: ActivationCardProps) {
	const form = useForm({ password: "", confirm_password: "" })

	const submit = (e: React.FormEvent) => {
		e.preventDefault()
		if (setPasswordUrl) form.post(setPasswordUrl)
	}

	return (
		<>
			<Head title={`Join ${invitation.teamName}`} />
			<Card className="w-full max-w-md">
				<CardHeader className="text-center">
					<div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
						<Users className="h-8 w-8 text-primary" />
					</div>
					<CardTitle>Set up your account</CardTitle>
					<CardDescription>
						<strong>{invitation.inviterName}</strong> invited <strong>{inviteeEmail ?? "you"}</strong> to join <strong>{invitation.teamName}</strong> as{" "}
						<span className="capitalize">{invitation.role}</span>.
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					{oidcOptions.length > 0 && (
						<div className="space-y-2">
							{oidcOptions.map((o) => (
								<Button key={o.provider} variant="outline" className="w-full" asChild>
									<a href={o.url}>Continue with {o.label}</a>
								</Button>
							))}
						</div>
					)}

					{allowPassword && oidcOptions.length > 0 && (
						<div className="relative">
							<div className="absolute inset-0 flex items-center">
								<span className="w-full border-t" />
							</div>
							<div className="relative flex justify-center text-xs uppercase">
								<span className="bg-background px-2 text-muted-foreground">or</span>
							</div>
						</div>
					)}

					{allowPassword && (
						<form onSubmit={submit} className="space-y-3">
							<div>
								<Label htmlFor="password">Create a password</Label>
								<Input
									id="password"
									type="password"
									autoComplete="new-password"
									value={form.data.password}
									onChange={(e) => form.setData("password", e.target.value)}
									className="mt-1"
								/>
								{form.errors.password && <p className="text-destructive text-sm">{form.errors.password}</p>}
								<p className="mt-1 text-muted-foreground text-xs">At least 12 characters.</p>
							</div>
							<div>
								<Label htmlFor="confirm_password">Confirm password</Label>
								<Input
									id="confirm_password"
									type="password"
									autoComplete="new-password"
									value={form.data.confirm_password}
									onChange={(e) => form.setData("confirm_password", e.target.value)}
									className="mt-1"
								/>
							</div>
							<Button type="submit" className="w-full" disabled={form.processing}>
								<KeyRound className="mr-2 h-4 w-4" />
								Set password &amp; continue
							</Button>
							<p className="flex items-center gap-1.5 text-muted-foreground text-xs">
								<ShieldCheck className="h-3.5 w-3.5 shrink-0" />
								You'll set up two-factor authentication next.
							</p>
						</form>
					)}

					{!allowPassword && oidcOptions.length === 0 && (
						<Alert>
							<AlertCircle className="h-4 w-4" />
							<AlertTitle>Sign-in unavailable</AlertTitle>
							<AlertDescription>No sign-in method is configured for this invitation. Contact your administrator.</AlertDescription>
						</Alert>
					)}
				</CardContent>
			</Card>
		</>
	)
}

AcceptInvitation.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
