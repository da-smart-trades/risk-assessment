// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { cn } from "@/lib/utils"

export function Logo({ className }: { className?: string }) {
	return <img src="/certora-logo.svg" className={className} alt="Certora" />
}

export function CertoraBanner({ className }: { className?: string }) {
	return (
		<>
			<img src="/certora-logo-with-text-black.svg" className={cn("dark:hidden", className)} alt="Certora" />
			<img src="/certora-logo-with-text-white.svg" className={cn("hidden dark:block", className)} alt="Certora" />
		</>
	)
}
