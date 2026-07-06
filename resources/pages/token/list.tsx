// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link } from "@inertiajs/react"
import type React from "react"
import { AssetLogo } from "@/components/asset-logo"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"
import { TOKEN_LOGOS } from "@/lib/logos"

const TOKEN_LABELS: Record<string, string> = {
	WETH: "WETH",
	USDE: "USDe",
	AAVE: "Aave (AAVE)",
	UNI: "Uniswap (UNI)",
	USDC: "USDC",
	USDT0: "USDT0",
	AUSDC: "aUSDC",
	CUSDC: "cUSDC",
	CBBTC: "cbBTC",
	LINK: "LINK",
	STETH: "stETH",
	WSTETH: "wstETH",
}

export default function TokenList({ tokens }: PagePropsFor<"token/list">) {
	return (
		<>
			<Head title="Tokens" />
			<Header title="Tokens" />
			<Container>
				<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
					{tokens.map((token) => (
						<Link key={token} href={route("tokens.show", { token_name: token })}>
							<Card className="transition-shadow hover:shadow-md">
								<CardHeader>
									<div className="flex items-center gap-3">
										<AssetLogo src={TOKEN_LOGOS[token]} name={token} size={28} />
										<CardTitle className="text-lg">{TOKEN_LABELS[token] ?? token}</CardTitle>
									</div>
								</CardHeader>
								<CardContent>
									<p className="text-muted-foreground text-sm">{token}</p>
								</CardContent>
							</Card>
						</Link>
					))}
				</div>
			</Container>
		</>
	)
}

TokenList.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
