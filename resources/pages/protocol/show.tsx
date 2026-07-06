// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head } from "@inertiajs/react"
import type React from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { ProtocolMetricsPanel } from "@/components/protocol-metrics-panel"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"

const PROTOCOL_LABELS: Record<string, string> = {
	AAVE_V3: "Aave v3",
	MORPHO_V2: "Morpho v2",
	COMPOUND_V3: "Compound v3",
	DRIFT_V2: "Drift v2",
}

export default function ProtocolShow({ protocol }: PagePropsFor<"protocol/show">) {
	const protocolStr = protocol as string
	const displayName = PROTOCOL_LABELS[protocolStr] ?? protocolStr

	return (
		<>
			<Head title={displayName} />
			<Header title={displayName} />
			<Container>
				<ProtocolMetricsPanel protocol={protocolStr} emptyMessage={`No manual metrics published for ${displayName} yet.`} />
			</Container>
		</>
	)
}

ProtocolShow.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
