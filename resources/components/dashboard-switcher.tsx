// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { router } from "@inertiajs/react"
import axios from "axios"
import { Check, ChevronsUpDown, MoreHorizontal, PlusIcon, Share2, Star, Trash2 } from "lucide-react"
import { useState } from "react"
import { useFavorites } from "@/components/favorites-provider"
import { Button } from "@/components/ui/button"
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator } from "@/components/ui/command"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import type { DashboardSummary } from "@/lib/generated/api/types.gen"
import { route } from "@/lib/generated/routes"
import { cn } from "@/lib/utils"
import { toast } from "./ui/use-toast"

interface Props {
	current: DashboardSummary
	dashboards: DashboardSummary[]
	canEdit: boolean
}

function goToBoard(id: string) {
	router.get(route("dashboard"), { board: id }, { preserveScroll: false })
}

export function DashboardSwitcher({ current, dashboards, canEdit }: Props) {
	const [open, setOpen] = useState(false)
	const [dialog, setDialog] = useState<null | "create" | "rename">(null)
	const [name, setName] = useState("")
	const [busy, setBusy] = useState(false)
	const { refetch: refetchFavorites } = useFavorites()

	const owned = dashboards.filter((d) => d.isOwner)
	const shared = dashboards.filter((d) => !d.isOwner)

	function openDialog(kind: "create" | "rename") {
		setName(kind === "rename" ? current.name : "")
		setDialog(kind)
		setOpen(false)
	}

	async function submitDialog(e: { preventDefault: () => void }) {
		e.preventDefault()
		const trimmed = name.trim()
		if (!trimmed) return
		setBusy(true)
		try {
			if (dialog === "create") {
				const { data } = await axios.post<DashboardSummary>("/api/dashboards", { name: trimmed })
				setDialog(null)
				await refetchFavorites()
				goToBoard(data.id)
			} else {
				await axios.patch(`/api/dashboards/${current.id}`, { name: trimmed })
				setDialog(null)
				await refetchFavorites()
				router.reload()
			}
		} catch {
			toast({ title: "Something went wrong", description: "Please try again.", variant: "destructive" })
		} finally {
			setBusy(false)
		}
	}

	async function toggleShare() {
		const next = current.isShared ? "private" : "team"
		try {
			await axios.patch(`/api/dashboards/${current.id}`, { visibility: next })
			toast({ title: next === "team" ? "Shared with your team" : "Made private", variant: "success" })
			await refetchFavorites()
			router.reload()
		} catch (err) {
			const detail = axios.isAxiosError(err) ? err.response?.data?.detail : undefined
			toast({ title: "Could not update sharing", description: detail ?? "Please try again.", variant: "destructive" })
		}
	}

	async function makeDefault() {
		await axios.patch(`/api/dashboards/${current.id}`, { isDefault: true })
		toast({ title: "Set as your default home page", variant: "success" })
		await refetchFavorites()
		router.reload()
	}

	async function remove() {
		if (!window.confirm(`Delete "${current.name}"? This can't be undone.`)) return
		await axios.delete(`/api/dashboards/${current.id}`)
		toast({ title: "Home page deleted", variant: "success" })
		await refetchFavorites()
		router.get(route("dashboard"))
	}

	return (
		<div className="flex items-center gap-2">
			<Popover open={open} onOpenChange={setOpen}>
				<PopoverTrigger asChild>
					<Button variant="outline" aria-expanded={open} className="min-w-52 justify-between gap-2">
						<span className="flex items-center gap-2 truncate">
							{current.isDefault && <Star className="h-3.5 w-3.5 shrink-0 fill-amber-500 text-amber-500" />}
							<span className="truncate">{current.name}</span>
						</span>
						<ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
					</Button>
				</PopoverTrigger>
				<PopoverContent className="w-64 p-0" align="start">
					<Command>
						<CommandInput placeholder="Find a home page…" />
						<CommandList>
							<CommandEmpty>No home pages found.</CommandEmpty>
							<CommandGroup heading="My home pages">
								{owned.map((d) => (
									<CommandItem key={d.id} onSelect={() => goToBoard(d.id)} className="gap-2">
										{d.isDefault && <Star className="h-3.5 w-3.5 fill-amber-500 text-amber-500" />}
										<span className="truncate">{d.name}</span>
										{d.isShared && <Share2 className="ml-auto h-3 w-3 text-muted-foreground" />}
										{d.id === current.id && <Check className={cn("h-4 w-4", d.isShared ? "ml-1" : "ml-auto")} />}
									</CommandItem>
								))}
							</CommandGroup>
							{shared.length > 0 && (
								<CommandGroup heading="Shared with me">
									{shared.map((d) => (
										<CommandItem key={d.id} onSelect={() => goToBoard(d.id)} className="gap-2">
											<span className="truncate">{d.name}</span>
											<span className="ml-auto truncate text-xs text-muted-foreground">{d.ownerName ?? "Team"}</span>
											{d.id === current.id && <Check className="ml-1 h-4 w-4" />}
										</CommandItem>
									))}
								</CommandGroup>
							)}
							<CommandSeparator />
							<CommandGroup>
								<CommandItem onSelect={() => openDialog("create")} className="gap-2">
									<PlusIcon className="h-4 w-4" />
									New home page
								</CommandItem>
							</CommandGroup>
						</CommandList>
					</Command>
				</PopoverContent>
			</Popover>

			{canEdit && (
				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button variant="ghost" size="icon" aria-label="Manage home page">
							<MoreHorizontal className="h-4 w-4" />
						</Button>
					</DropdownMenuTrigger>
					<DropdownMenuContent align="end" className="w-52">
						<DropdownMenuItem onSelect={() => openDialog("rename")}>Rename</DropdownMenuItem>
						<DropdownMenuItem onSelect={toggleShare}>
							<Share2 className="mr-2 h-4 w-4" />
							{current.isShared ? "Make private" : "Share with team"}
						</DropdownMenuItem>
						{!current.isDefault && <DropdownMenuItem onSelect={makeDefault}>Set as default</DropdownMenuItem>}
						<DropdownMenuSeparator />
						<DropdownMenuItem onSelect={remove} className="text-destructive focus:text-destructive">
							<Trash2 className="mr-2 h-4 w-4" />
							Delete
						</DropdownMenuItem>
					</DropdownMenuContent>
				</DropdownMenu>
			)}

			<Dialog open={dialog !== null} onOpenChange={(o) => !o && setDialog(null)}>
				<DialogContent>
					<form onSubmit={submitDialog}>
						<DialogHeader>
							<DialogTitle>{dialog === "rename" ? "Rename home page" : "New home page"}</DialogTitle>
							<DialogDescription>
								{dialog === "rename" ? "Give this home page a new name." : "Name a new home page. You can pin favorites to it and share it with your team."}
							</DialogDescription>
						</DialogHeader>
						<div className="grid gap-2 py-4">
							<Label htmlFor="dashboard-name">Name</Label>
							<Input id="dashboard-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Stablecoin monitoring" autoFocus maxLength={120} />
						</div>
						<DialogFooter>
							<Button type="submit" disabled={busy || !name.trim()}>
								{dialog === "rename" ? "Save" : "Create"}
							</Button>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>
		</div>
	)
}
