// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head } from "@inertiajs/react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import ManualMetricForm from "@/pages/manual-metrics/admin/partials/manual-metric-form"

export default function ManualMetricsEdit({ items, isOperatorEditor }: PagePropsFor<"manual-metrics/admin/edit">["content"]) {
	const metric = items[0]
	if (!metric) {
		return (
			<>
				<Head title="Edit manual metric" />
				<Header title="Edit manual metric" />
				<Container>
					<p>Manual metric not found.</p>
				</Container>
			</>
		)
	}

	return (
		<>
			<Head title={`Edit ${metric.name}`} />
			<Header title="Edit manual metric" />
			<Container>
				<div className="max-w-3xl">
					<ManualMetricForm
						isOperatorEditor={isOperatorEditor}
						scopeLabel={metric.teamId ? (metric.teamName ?? "Team") : "Shared (platform-wide)"}
						initial={{
							id: metric.id,
							name: metric.name,
							desc: metric.desc,
							category: metric.category,
							chain: metric.chain ?? null,
							token: metric.token ?? null,
							protocol: metric.protocol ?? null,
							subCategory: metric.subCategory ?? null,
							value: metric.value ?? null,
							riskScore: metric.riskScore ?? null,
							notes: metric.notes ?? null,
							marketChainId: metric.marketChainId ?? null,
							marketIdHex: metric.marketIdHex ?? null,
							teamId: metric.teamId ?? null,
							teamName: metric.teamName ?? null,
							isPublished: metric.isPublished ?? false,
						}}
					/>
				</div>
			</Container>
		</>
	)
}

ManualMetricsEdit.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
