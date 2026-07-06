// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head } from "@inertiajs/react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import ManualMetricForm from "@/pages/manual-metrics/admin/partials/manual-metric-form"

export default function ManualMetricsCreate({ teams, isOperatorEditor }: PagePropsFor<"manual-metrics/admin/create">["content"]) {
	// Scope is server-derived. Show the user what scope this metric will land in:
	//   - operator editor → shared (platform-wide)
	//   - non-operator → their (sole) editable team
	const teamScope = teams.find((t) => !t.isShared && t.canEdit)
	const scopeLabel = isOperatorEditor ? "Shared (platform-wide)" : (teamScope?.teamName ?? "—")

	return (
		<>
			<Head title="New manual metric" />
			<Header title="New manual metric" />
			<Container>
				<div className="max-w-3xl">
					<ManualMetricForm scopeLabel={scopeLabel} isOperatorEditor={isOperatorEditor} />
				</div>
			</Container>
		</>
	)
}

ManualMetricsCreate.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
