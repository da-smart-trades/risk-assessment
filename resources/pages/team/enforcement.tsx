// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, router, useForm } from "@inertiajs/react"
import { AlertTriangle, Lock, Mail, ShieldCheck, Unlock } from "lucide-react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { AppLayout } from "@/layouts/app-layout"
import { route } from "@/lib/generated/routes"

interface StuckMember {
	id: string
	email: string
	activatedAt: string | null
	hasActiveLockout: boolean
}

interface Props {
	teamId: string
	teamName: string
	enforcedProvider: string | null
	currentAuthMethod: string | null
	availableProviders: string[]
	stuckMembers: StuckMember[]
}

const PROVIDER_LABELS: Record<string, string> = {
	google: "Google",
	microsoft: "Microsoft",
	github: "GitHub",
}

const label = (provider: string | null): string => (provider ? (PROVIDER_LABELS[provider] ?? provider) : "Any provider")

export default function TeamEnforcement({ teamId, teamName, enforcedProvider, currentAuthMethod, availableProviders, stuckMembers }: Props) {
	const form = useForm<{ provider: string }>({ provider: enforcedProvider ?? "" })

	const submit = (provider: string) => {
		form.setData("provider", provider)
		router.post(
			route("team.enforcement.update", { team_id: teamId }),
			{ provider },
			{
				preserveScroll: true,
				onSuccess: () => toast({ description: provider ? `${label(provider)} sign-in is now required.` : "Enforcement removed.", variant: "success" }),
				onError: (errors) => toast({ description: Object.values(errors)[0] ?? "Could not update enforcement.", variant: "destructive" }),
			},
		)
	}

	const sendReminder = (memberId: string) => {
		router.post(
			route("team.enforcement.remind", { team_id: teamId }),
			{ member_id: memberId },
			{ preserveScroll: true, onSuccess: () => toast({ description: "Reminder sent.", variant: "success" }) },
		)
	}

	const actingProviderMismatch = enforcedProvider === null && currentAuthMethod !== null && !["google", "microsoft", "github"].includes(currentAuthMethod)

	return (
		<>
			<Head title={`${teamName} — Sign-in enforcement`} />
			<Header title="Sign-in enforcement" />
			<Container>
				<div className="mx-auto max-w-2xl space-y-6">
					<Card>
						<CardHeader>
							<CardTitle className="flex items-center gap-2">
								<ShieldCheck className="h-5 w-5" />
								Required sign-in provider
							</CardTitle>
							<CardDescription>
								Lock <strong>{teamName}</strong> to a single identity provider. Members can then sign in only via that provider; everyone else is guided through a one-time
								migration on their next sign-in.
							</CardDescription>
						</CardHeader>
						<CardContent className="space-y-4">
							<div className="flex items-center gap-2 text-sm">
								<span className="text-muted-foreground">Current policy:</span>
								{enforcedProvider ? (
									<Badge variant="default" className="gap-1">
										<Lock className="h-3 w-3" />
										{label(enforcedProvider)} required
									</Badge>
								) : (
									<Badge variant="secondary" className="gap-1">
										<Unlock className="h-3 w-3" />
										No enforcement
									</Badge>
								)}
							</div>

							<div className="flex flex-wrap items-end gap-3">
								<div className="min-w-48">
									<Select value={form.data.provider || "__none__"} onValueChange={(v) => form.setData("provider", v === "__none__" ? "" : v)}>
										<SelectTrigger>
											<SelectValue placeholder="Choose a provider" />
										</SelectTrigger>
										<SelectContent>
											<SelectItem value="__none__">No enforcement</SelectItem>
											{availableProviders.map((p) => (
												<SelectItem key={p} value={p}>
													{label(p)}
												</SelectItem>
											))}
										</SelectContent>
									</Select>
								</div>
								<Button onClick={() => submit(form.data.provider)} disabled={form.processing || form.data.provider === (enforcedProvider ?? "")}>
									Save policy
								</Button>
							</div>

							<p className="text-muted-foreground text-xs">
								To enforce a provider you must currently be signed in via that provider
								{currentAuthMethod ? ` (you are signed in via ${label(currentAuthMethod)})` : ""}, and every other team owner must already have it linked.
							</p>
							{actingProviderMismatch && (
								<p className="flex items-center gap-1 text-amber-600 text-xs">
									<AlertTriangle className="h-3 w-3" />
									Sign in with the target provider first to enforce it.
								</p>
							)}
						</CardContent>
					</Card>

					{enforcedProvider && (
						<Card>
							<CardHeader>
								<CardTitle className="flex items-center gap-2">
									<AlertTriangle className="h-5 w-5" />
									Members still to migrate
									{stuckMembers.length > 0 && <Badge variant="destructive">{stuckMembers.length}</Badge>}
								</CardTitle>
								<CardDescription>Members who haven't signed in via {label(enforcedProvider)} since enforcement was set.</CardDescription>
							</CardHeader>
							<CardContent>
								{stuckMembers.length === 0 ? (
									<p className="text-muted-foreground text-sm">Everyone has migrated. 🎉</p>
								) : (
									<div className="space-y-2">
										{stuckMembers.map((member) => (
											<div key={member.id} className="flex items-center justify-between rounded-lg border p-3">
												<div className="flex items-center gap-2">
													<span className="font-medium text-sm">{member.email}</span>
													{member.hasActiveLockout && (
														<Badge variant="outline" className="text-xs">
															Locked out
														</Badge>
													)}
													{member.activatedAt === null && (
														<Badge variant="secondary" className="text-xs">
															Never signed in
														</Badge>
													)}
												</div>
												<Button variant="outline" size="sm" onClick={() => sendReminder(member.id)}>
													<Mail className="mr-2 h-4 w-4" />
													Send reminder
												</Button>
											</div>
										))}
									</div>
								)}
							</CardContent>
						</Card>
					)}
				</div>
			</Container>
		</>
	)
}

TeamEnforcement.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
