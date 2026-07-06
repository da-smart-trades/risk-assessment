// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import * as React from "react"
import { Container } from "@/components/container"
import { cn } from "@/lib/utils"

interface HeaderProps extends React.HTMLAttributes<HTMLDivElement> {
	title?: string
	subtitle?: React.ReactNode
	icon?: React.ReactNode
	/** Right-aligned slot for action buttons (alias of ``children``). */
	actions?: React.ReactNode
	children?: React.ReactNode
}

const Header = React.forwardRef<HTMLDivElement, HeaderProps>(({ className, title, subtitle, icon, actions, children, ...props }, ref) => (
	<div ref={ref} className={cn("mb-12 border-b bg-background py-4 sm:py-8", className)} {...props}>
		<Container>
			<div className="flex items-center justify-between gap-4">
				<div className="min-w-0">
					<h1 className="heading-uppercase text-xl sm:text-2xl flex items-center gap-3">
						{icon}
						<span className="text-brand-gradient">{title}</span>
					</h1>
					{subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
				</div>
				{(actions || children) && (
					<div className="flex shrink-0 items-center gap-2">
						{actions}
						{children}
					</div>
				)}
			</div>
		</Container>
	</div>
))
Header.displayName = "Header"

export { Header }
