// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { useForm } from "@inertiajs/react"
import { InputError } from "@/components/input-error"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { route } from "@/lib/generated/routes"

export default function CreateTeamForm() {
	const { data, setData, post, processing, errors } = useForm({
		name: "",
		description: "",
		domain: "",
	})

	const submit = (e: React.FormEvent) => {
		e.preventDefault()
		post(route("teams.add"), {
			onSuccess: () => {
				toast({
					title: "Team Created",
					description: "Your new team has been created.",
					variant: "success",
				})
			},
		})
	}

	return (
		<Card>
			<CardHeader>
				<CardTitle>Team Details</CardTitle>
				<CardDescription>Create a new team to collaborate with others.</CardDescription>
			</CardHeader>
			<CardContent>
				<form onSubmit={submit} className="space-y-6">
					<div>
						<Label htmlFor="name">Team Name</Label>
						<Input id="name" value={data.name} onChange={(e) => setData("name", e.target.value)} className="mt-1" placeholder="My Awesome Team" required />
						<InputError message={errors.name} className="mt-2" />
					</div>

					<div>
						<Label htmlFor="description">Description (optional)</Label>
						<Textarea
							id="description"
							value={data.description}
							onChange={(e) => setData("description", e.target.value)}
							className="mt-1"
							placeholder="What is this team for?"
							rows={3}
						/>
						<InputError message={errors.description} className="mt-2" />
					</div>

					<div>
						<Label htmlFor="domain">Email Domain (optional)</Label>
						<Input id="domain" value={data.domain} onChange={(e) => setData("domain", e.target.value)} className="mt-1" placeholder="certora.com" />
						<p className="mt-1 text-muted-foreground text-xs">
							If set, only users with an email at this domain (e.g. <code>name@certora.com</code>) can be invited. This cannot be changed after the team is created.
						</p>
						<InputError message={errors.domain} className="mt-2" />
					</div>

					<Button type="submit" disabled={processing}>
						Create Team
					</Button>
				</form>
			</CardContent>
		</Card>
	)
}
