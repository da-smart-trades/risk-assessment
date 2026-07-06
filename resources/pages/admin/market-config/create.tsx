// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head } from "@inertiajs/react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { AdminLayout } from "@/layouts/admin-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { MarketConfigForm } from "./partials/market-config-form"

export default function MarketConfigCreate({ protocolOptions }: PagePropsFor<"admin/market-config/create">) {
	return (
		<>
			<Head title="Add protocol" />
			<Header title="Add protocol" />
			<Container>
				<MarketConfigForm
					mode="create"
					initial={{
						protocol: "",
						enabled: true,
						assuranceProtocol: null,
					}}
					protocolOptions={protocolOptions ?? []}
				/>
			</Container>
		</>
	)
}

MarketConfigCreate.layout = (page: React.ReactNode) => <AdminLayout>{page}</AdminLayout>
