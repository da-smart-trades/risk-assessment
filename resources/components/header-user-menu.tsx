// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { router, usePage } from "@inertiajs/react"
import { LogOut, User } from "lucide-react"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import type { FullSharedProps } from "@/lib/generated/page-props"
import { route } from "@/lib/generated/routes"
import { getGravatarUrl, getInitials } from "@/lib/utils"

export function HeaderUserMenu() {
	const { auth } = usePage<FullSharedProps>().props

	if (!auth?.user) {
		return null
	}

	const user = auth.user
	const displayName = user.name || user.email
	const initials = getInitials(displayName)
	const avatarSrc = user.avatarUrl ?? getGravatarUrl(user.email)

	return (
		<DropdownMenu>
			<DropdownMenuTrigger
				className="rounded-full ring-offset-background transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 data-[state=open]:ring-2 data-[state=open]:ring-ring"
				aria-label="Open user menu"
			>
				<Avatar className="h-9 w-9">
					<AvatarImage src={avatarSrc} alt={displayName} />
					<AvatarFallback>{initials}</AvatarFallback>
				</Avatar>
			</DropdownMenuTrigger>
			<DropdownMenuContent className="w-56 rounded-lg" align="end" sideOffset={8}>
				<DropdownMenuLabel className="p-0 font-normal">
					<div className="flex items-center gap-2 px-1 py-1.5 text-left text-sm">
						<Avatar className="h-8 w-8 rounded-lg">
							<AvatarImage src={avatarSrc} alt={displayName} />
							<AvatarFallback className="rounded-lg">{initials}</AvatarFallback>
						</Avatar>
						<div className="grid flex-1 text-left text-sm leading-tight">
							<span className="truncate font-medium">{displayName}</span>
							<span className="truncate text-xs text-muted-foreground">{user.email}</span>
						</div>
					</div>
				</DropdownMenuLabel>
				<DropdownMenuSeparator />
				<DropdownMenuItem className="cursor-pointer" onClick={() => router.visit(route("profile.show"))}>
					<User className="size-4" />
					Profile
				</DropdownMenuItem>
				<DropdownMenuSeparator />
				<DropdownMenuItem className="cursor-pointer" onClick={() => router.post(route("logout"))}>
					<LogOut className="size-4" />
					Log out
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	)
}
