// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head } from "@inertiajs/react"
import type React from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { AppLayout } from "@/layouts/app-layout"
import type { PagePropsFor } from "@/lib/generated/page-props"

export default function About({ commitSha }: PagePropsFor<"about">["content"]) {
	return (
		<>
			<Head title="About Us" />
			<section className="relative flex w-full flex-1 items-center justify-center overflow-hidden">
				<div
					className="absolute inset-0 animate-starfield motion-reduce:animate-none"
					style={{
						backgroundImage: [
							"radial-gradient(1px 1px at 20% 30%, rgba(32,34,53,0.25) 0, transparent 60%)",
							"radial-gradient(1px 1px at 70% 20%, rgba(32,34,53,0.2) 0, transparent 60%)",
							"radial-gradient(1.5px 1.5px at 40% 60%, rgba(32,34,53,0.18) 0, transparent 60%)",
							"radial-gradient(1px 1px at 80% 75%, rgba(32,34,53,0.2) 0, transparent 60%)",
							"radial-gradient(1.5px 1.5px at 55% 85%, rgba(32,34,53,0.22) 0, transparent 60%)",
						].join(","),
					}}
				/>
				<div
					className="absolute inset-0 animate-twinkle motion-reduce:animate-none"
					style={{
						backgroundImage: [
							"radial-gradient(2px 2px at 12% 25%, rgba(32,34,53,0.4) 0, transparent 60%)",
							"radial-gradient(1.5px 1.5px at 28% 40%, rgba(32,34,53,0.35) 0, transparent 60%)",
							"radial-gradient(2px 2px at 68% 22%, rgba(32,34,53,0.4) 0, transparent 60%)",
							"radial-gradient(1.5px 1.5px at 82% 60%, rgba(32,34,53,0.35) 0, transparent 60%)",
							"radial-gradient(2px 2px at 45% 78%, rgba(32,34,53,0.35) 0, transparent 60%)",
						].join(","),
					}}
				/>
				<div
					className="absolute inset-0 animate-twinkle-fast motion-reduce:animate-none"
					style={{
						backgroundImage: [
							"radial-gradient(1.5px 1.5px at 18% 55%, rgba(32,34,53,0.35) 0, transparent 60%)",
							"radial-gradient(1px 1px at 33% 18%, rgba(32,34,53,0.3) 0, transparent 60%)",
							"radial-gradient(1.5px 1.5px at 57% 35%, rgba(32,34,53,0.35) 0, transparent 60%)",
							"radial-gradient(1px 1px at 76% 82%, rgba(32,34,53,0.3) 0, transparent 60%)",
							"radial-gradient(1.5px 1.5px at 90% 30%, rgba(32,34,53,0.35) 0, transparent 60%)",
						].join(","),
					}}
				/>
				<div className="absolute inset-0 bg-gradient-to-t from-background via-background/70 to-transparent" />
				<div
					className="absolute inset-0 animate-glow-pulse motion-reduce:animate-none"
					style={{
						backgroundImage: [
							"radial-gradient(2px 2px at 10% 15%, rgba(32,34,53,0.35) 0, transparent 60%)",
							"radial-gradient(1.5px 1.5px at 25% 45%, rgba(32,34,53,0.25) 0, transparent 60%)",
							"radial-gradient(1.5px 1.5px at 60% 30%, rgba(32,34,53,0.28) 0, transparent 60%)",
							"radial-gradient(2px 2px at 75% 55%, rgba(32,34,53,0.3) 0, transparent 60%)",
							"radial-gradient(1.5px 1.5px at 85% 20%, rgba(32,34,53,0.25) 0, transparent 60%)",
							"radial-gradient(2px 2px at 35% 80%, rgba(32,34,53,0.22) 0, transparent 60%)",
							"radial-gradient(1.5px 1.5px at 90% 85%, rgba(32,34,53,0.24) 0, transparent 60%)",
						].join(","),
					}}
				/>
				<div className="absolute inset-0 flex flex-col items-center justify-center gap-8">
					<div className="text-center text-sm font-semibold uppercase tracking-[0.3em] text-foreground/70">Certora Blockchain Risk Assessment</div>
					<div className="flex w-full max-w-xs flex-col items-center gap-1.5">
						<Label htmlFor="commit-sha" className="text-muted-foreground text-xs uppercase tracking-widest">
							Build commit
						</Label>
						<Input id="commit-sha" readOnly value={commitSha} onFocus={(e) => e.currentTarget.select()} className="text-center font-mono" />
					</div>
						<p className="max-w-md text-center text-muted-foreground text-xs">
							© 2026 Certora. Licensed under the{" "}
							<a
								href="https://www.gnu.org/licenses/agpl-3.0.html"
								target="_blank"
								rel="noreferrer"
								className="underline underline-offset-4 hover:text-foreground"
							>
								GNU Affero General Public License v3.0
							</a>
							. The complete source for this version is at{" "}
							<a
								href="https://github.com/Certora/risk-assessment"
								target="_blank"
								rel="noreferrer"
								className="underline underline-offset-4 hover:text-foreground"
							>
								github.com/Certora/risk-assessment
							</a>
							.
						</p>
				</div>
			</section>
		</>
	)
}

About.layout = (page: React.ReactNode) => <AppLayout mainClassName="pb-0 flex flex-col">{page}</AppLayout>
