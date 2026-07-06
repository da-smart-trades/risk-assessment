// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head } from "@inertiajs/react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { AdminLayout } from "@/layouts/admin-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { WeightingProfileForm } from "./partials/weighting-profile-form"

export default function WeightingProfileEdit({ profile, scopes, markets, isOperatorEditor }: PagePropsFor<"admin/weighting-profiles/edit">) {
	return (
		<>
			<Head title={`Edit ${profile.name}`} />
			<Header title={`Edit ${profile.name}`} subtitle={profile.teamName ?? "Global default"} />
			<Container>
				<WeightingProfileForm
					mode="edit"
					initial={{
						id: profile.id,
						teamId: profile.teamId,
						isGlobal: profile.teamId === null,
						name: profile.name,
						scope: profile.scope,
						targetProtocol: profile.targetProtocol,
						targetMarketConfigId: profile.targetMarketConfigId,
						targetChainId: profile.targetChainId,
						targetMarketIdHex: profile.targetMarketIdHex,
						targetLabel: profile.targetMarketLabel,
						entries: profile.entries.map((e) => ({
							id: e.id ?? undefined,
							category: e.category,
							subCategory: e.subCategory,
							weight: String(e.weight),
						})),
					}}
					scopes={scopes}
					markets={markets}
					isOperatorEditor={isOperatorEditor}
				/>
			</Container>
		</>
	)
}

WeightingProfileEdit.layout = (page: React.ReactNode) => <AdminLayout>{page}</AdminLayout>
