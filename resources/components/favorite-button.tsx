// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Check, Plus, Star } from "lucide-react"
import { useState } from "react"
import { type FavoriteTarget, useFavorites } from "@/components/favorites-provider"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn } from "@/lib/utils"

interface Props {
	target: FavoriteTarget
	className?: string
	size?: "sm" | "md"
}

export function FavoriteButton({ target, className, size = "sm" }: Props) {
	const { loading, boards, isFavoritedOn, isFavoritedAnywhere, toggleOn, createBoardAndPin } = useFavorites()
	const [open, setOpen] = useState(false)
	const [busyId, setBusyId] = useState<string | null>(null)
	const favorited = isFavoritedAnywhere(target)
	const sizeClasses = size === "sm" ? "h-4 w-4" : "h-5 w-5"

	async function onToggleBoard(boardId: string) {
		if (busyId) return
		setBusyId(boardId)
		try {
			await toggleOn(target, boardId)
		} finally {
			setBusyId(null)
		}
	}

	async function onCreateAndPin() {
		if (busyId) return
		setBusyId("__create__")
		try {
			await createBoardAndPin("My favorites", target)
		} finally {
			setBusyId(null)
		}
	}

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<button
					type="button"
					disabled={loading}
					aria-pressed={favorited}
					aria-label={favorited ? "Edit pinned home pages" : "Pin to a home page"}
					className={cn(
						"inline-flex items-center justify-center rounded-md p-1 transition-colors",
						"text-muted-foreground hover:text-amber-500 disabled:cursor-not-allowed disabled:opacity-50",
						favorited && "text-amber-500",
						className,
					)}
				>
					<Star className={cn(sizeClasses, favorited && "fill-amber-500")} />
				</button>
			</PopoverTrigger>
			<PopoverContent align="end" className="w-60 p-1">
				<div className="px-2 py-1.5 font-medium text-muted-foreground text-xs">Pin to home page</div>
				{boards.length === 0 ? (
					<button
						type="button"
						onClick={onCreateAndPin}
						disabled={busyId !== null}
						className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent disabled:opacity-50"
					>
						<Plus className="h-4 w-4 shrink-0" />
						<span className="truncate">Create “My favorites” &amp; pin</span>
					</button>
				) : (
					<div className="max-h-64 overflow-y-auto">
						{boards.map((b) => {
							const pinned = isFavoritedOn(target, b.id) !== undefined
							return (
								<button
									key={b.id}
									type="button"
									onClick={() => onToggleBoard(b.id)}
									disabled={busyId !== null}
									className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent disabled:opacity-50"
								>
									<span
										className={cn(
											"flex h-4 w-4 shrink-0 items-center justify-center rounded border",
											pinned ? "border-amber-500 bg-amber-500 text-white" : "border-muted-foreground/40",
										)}
									>
										{pinned && <Check className="h-3 w-3" />}
									</span>
									<span className="truncate">{b.name}</span>
									{b.isDefault && <span className="ml-auto shrink-0 text-muted-foreground text-xs">Default</span>}
								</button>
							)
						})}
					</div>
				)}
			</PopoverContent>
		</Popover>
	)
}
