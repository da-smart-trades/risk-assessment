// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link, useForm } from "@inertiajs/react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { InputError } from "@/components/input-error"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { AppLayout } from "@/layouts/app-layout"
import { route } from "@/lib/generated/routes"

export default function SecurityReportsAdminUpload() {
	const form = useForm<{ name: string; description: string; file: File | null }>({
		name: "",
		description: "",
		file: null,
	})

	const handleSubmit = (e: React.FormEvent) => {
		e.preventDefault()
		form.post(route("security_reports.admin.upload"), { forceFormData: true })
	}

	return (
		<>
			<Head title="Upload Security Report" />
			<Header title="Upload Security Report" />
			<Container>
				<div className="max-w-2xl">
					<form onSubmit={handleSubmit} className="space-y-6">
						<div className="space-y-2">
							<Label htmlFor="name">Name</Label>
							<Input id="name" value={form.data.name} onChange={(e) => form.setData("name", e.target.value)} placeholder="e.g. Ethereum Protocol Audit Q1 2026" required />
							<InputError message={form.errors.name} />
						</div>

						<div className="space-y-2">
							<Label htmlFor="description">Description</Label>
							<Textarea
								id="description"
								value={form.data.description}
								onChange={(e) => form.setData("description", e.target.value)}
								placeholder="Brief summary of what this report covers…"
								rows={3}
								required
							/>
							<InputError message={form.errors.description} />
						</div>

						<div className="space-y-2">
							<Label htmlFor="file">PDF File</Label>
							<Input id="file" type="file" accept=".pdf,application/pdf" onChange={(e) => form.setData("file", e.target.files?.[0] ?? null)} required />
							<p className="text-muted-foreground text-xs">PDF only, max 50 MB.</p>
							<InputError message={form.errors.file} />
						</div>

						<div className="flex items-center gap-3">
							<Button type="submit" disabled={form.processing}>
								{form.processing ? "Uploading…" : "Upload report"}
							</Button>
							<Link href={route("security_reports.admin.list")}>
								<Button type="button" variant="ghost">
									Cancel
								</Button>
							</Link>
						</div>
					</form>
				</div>
			</Container>
		</>
	)
}

SecurityReportsAdminUpload.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
