// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { cn } from "@/lib/utils"

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
	return <div data-slot="skeleton" className={cn("rounded-md bg-muted animate-pulse", className)} {...props} />
}

export { Skeleton }
