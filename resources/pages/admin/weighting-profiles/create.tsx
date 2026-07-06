// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head } from "@inertiajs/react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { AdminLayout } from "@/layouts/admin-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { WeightingProfileForm } from "./partials/weighting-profile-form"

export default function WeightingProfileCreate({ scopes, markets, isOperatorEditor }: PagePropsFor<"admin/weighting-profiles/create">) {
	return (
		<>
			<Head title="New weighting profile" />
			<Header title="New weighting profile" />
			<Container>
				<WeightingProfileForm
					mode="create"
					initial={{
						teamId: null,
						isGlobal: isOperatorEditor,
						name: "",
						scope: "MARKET",
						targetProtocol: null,
						targetMarketConfigId: null,
						targetChainId: null,
						targetMarketIdHex: null,
						targetLabel: null,
						entries: [],
					}}
					scopes={scopes}
					markets={markets}
					isOperatorEditor={isOperatorEditor}
				/>
			</Container>
		</>
	)
}

WeightingProfileCreate.layout = (page: React.ReactNode) => <AdminLayout>{page}</AdminLayout>
