// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head } from "@inertiajs/react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { AdminLayout } from "@/layouts/admin-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { MarketConfigForm } from "./partials/market-config-form"

export default function MarketConfigEdit({ market, protocolOptions }: PagePropsFor<"admin/market-config/edit">) {
	return (
		<>
			<Head title={`Edit ${market.protocol}`} />
			<Header title={`Edit ${market.protocol}`} subtitle="Protocol" />
			<Container>
				<MarketConfigForm
					mode="edit"
					initial={{
						id: market.id,
						protocol: market.protocol,
						enabled: market.enabled,
						assuranceProtocol: market.assuranceProtocol ?? null,
					}}
					protocolOptions={protocolOptions ?? []}
				/>
			</Container>
		</>
	)
}

MarketConfigEdit.layout = (page: React.ReactNode) => <AdminLayout>{page}</AdminLayout>
