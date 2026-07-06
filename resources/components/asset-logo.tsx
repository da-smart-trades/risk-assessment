// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { cn } from "@/lib/utils"

interface AssetLogoProps {
	src?: string
	name: string
	size?: number
	className?: string
}

export function AssetLogo({ src, name, size = 24, className }: AssetLogoProps) {
	if (src) {
		return <img src={src} alt={name} width={size} height={size} className={cn("shrink-0 rounded-full object-contain", className)} />
	}
	return (
		<span
			aria-hidden
			className={cn("inline-flex shrink-0 items-center justify-center rounded-full bg-muted font-semibold text-muted-foreground", className)}
			style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }}
		>
			{name.slice(0, 2)}
		</span>
	)
}
