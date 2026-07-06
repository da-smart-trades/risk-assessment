// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { router, useForm } from "@inertiajs/react"
import { Trash2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { InputError } from "@/components/input-error"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { formatChainId } from "@/lib/chain-labels"
import { route } from "@/lib/generated/routes"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type CategoryKey = "ANCHOR" | "CONTROL" | "ASSURANCE"
type ScopeKey = "MARKET" | "PROTOCOL"

interface MarketOption {
	marketConfigId: string
	protocol: string
	chainId: number
	marketIdHex: string
	label: string
}

interface ScopeOption {
	teamId: string | null
	teamSlug: string
	teamName: string
	isGlobal: boolean
	canEdit: boolean
}

export interface WeightingProfileEntryInput {
	id?: string
	category: CategoryKey
	subCategory: string
	weight: string
}

export interface WeightingProfileInitial {
	id?: string
	teamId: string | null
	isGlobal: boolean
	name: string
	scope: ScopeKey
	targetProtocol: string | null
	targetMarketConfigId: string | null
	targetChainId: number | null
	targetMarketIdHex: string | null
	targetLabel: string | null
	entries: WeightingProfileEntryInput[]
}

interface Props {
	mode: "create" | "edit"
	initial: WeightingProfileInitial
	scopes: ScopeOption[]
	markets: MarketOption[]
	isOperatorEditor: boolean
}

const CATEGORY_OPTIONS: { value: CategoryKey; label: string }[] = [
	{ value: "ANCHOR", label: "Anchor" },
	{ value: "CONTROL", label: "Control" },
	{ value: "ASSURANCE", label: "Assurance" },
]

// Composite Select value: the form picker can't distinguish two markets
// that share a protocol row by marketConfigId alone, so we encode the
// trio that uniquely identifies a market and decode on submit.
const marketKey = (m: { marketConfigId: string; chainId: number; marketIdHex: string }): string => `${m.marketConfigId}|${m.chainId}|${m.marketIdHex}`

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Shared form for creating / editing a weighting profile.
 *
 * Header lets the operator pick scope (per-market or per-protocol)
 * and the relevant target. The entries section is an array of
 * `(category, sub_category, weight)` rows. The sub-category dropdown
 * is populated dynamically from `/api/weighting-profiles/
 * available-sub-categories` so the operator can only pick keys that
 * are actually produced by the scorer (or, for assurance, declared on
 * a manual metric).
 */
export function WeightingProfileForm({ mode, initial, scopes, markets, isOperatorEditor }: Props) {
	const initialMarketKey =
		initial.targetMarketConfigId && initial.targetChainId !== null && initial.targetMarketIdHex
			? `${initial.targetMarketConfigId}|${initial.targetChainId}|${initial.targetMarketIdHex}`
			: ""
	const form = useForm({
		isGlobal: initial.isGlobal,
		name: initial.name,
		scope: initial.scope,
		targetProtocol: initial.targetProtocol ?? "",
		targetMarketKey: initialMarketKey,
		entries: initial.entries,
	})

	const isEdit = mode === "edit"
	const protocols = useMemo(() => {
		const set = new Set<string>()
		for (const m of markets) set.add(m.protocol)
		return Array.from(set).sort()
	}, [markets])

	const selectedMarket = useMemo(() => markets.find((m) => marketKey(m) === form.data.targetMarketKey) ?? null, [markets, form.data.targetMarketKey])

	const submit = (event: React.FormEvent) => {
		event.preventDefault()
		const isMarketScope = form.data.scope === "MARKET"
		const payload: Record<string, unknown> = {
			isGlobal: form.data.isGlobal,
			name: form.data.name,
			scope: form.data.scope,
			targetProtocol: !isMarketScope ? form.data.targetProtocol : null,
			targetMarketConfigId: isMarketScope ? (selectedMarket?.marketConfigId ?? null) : null,
			targetChainId: isMarketScope ? (selectedMarket?.chainId ?? null) : null,
			targetMarketIdHex: isMarketScope ? (selectedMarket?.marketIdHex ?? null) : null,
			targetLabel: isMarketScope ? (selectedMarket?.label ?? null) : null,
			entries: form.data.entries.map((e) => ({
				category: e.category,
				subCategory: e.subCategory,
				weight: e.weight === "" ? 1 : Number(e.weight),
			})),
		}
		if (mode === "create") {
			router.post(route("admin.weighting_profiles.create"), payload)
		} else if (initial.id) {
			router.patch(route("admin.weighting_profiles.update", { profile_id: initial.id }), payload)
		}
	}

	const onDelete = () => {
		if (!isEdit || !initial.id) return
		if (!confirm("Delete this weighting profile? PD will fall back to the next-most-specific profile.")) {
			return
		}
		router.delete(route("admin.weighting_profiles.delete", { profile_id: initial.id }), {
			preserveScroll: true,
		})
	}

	const addEntry = () => {
		form.setData("entries", [...form.data.entries, { category: "ANCHOR", subCategory: "", weight: "1.0" }])
	}

	const removeEntry = (idx: number) => {
		form.setData(
			"entries",
			form.data.entries.filter((_, i) => i !== idx),
		)
	}

	const updateEntry = (idx: number, field: keyof WeightingProfileEntryInput, value: string) => {
		form.setData(
			"entries",
			form.data.entries.map((entry, i) => (i === idx ? ({ ...entry, [field]: value } as WeightingProfileEntryInput) : entry)),
		)
	}

	const targetProtocol = form.data.scope === "MARKET" ? (selectedMarket?.protocol ?? null) : form.data.targetProtocol || null

	return (
		<form onSubmit={submit} className="space-y-6">
			<Card>
				<CardHeader>
					<CardTitle>{isEdit ? "Edit weighting profile" : "New weighting profile"}</CardTitle>
					<CardDescription>Override the default weight of 1.0 for specific PD inputs. Any (category, sub-category) combination not listed below keeps the default.</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					<div className="space-y-1">
						<Label htmlFor="name">Name</Label>
						<Input id="name" value={form.data.name} onChange={(e) => form.setData("name", e.target.value)} placeholder="e.g. Conservative Aave weights" required />
						<InputError message={form.errors.name} />
					</div>
					<div className="grid grid-cols-1 md:grid-cols-2 gap-4">
						<div className="space-y-1">
							<Label>Team scope</Label>
							<Select
								value={form.data.isGlobal ? "__global__" : form.data.isGlobal === false && initial.teamId ? initial.teamId : "__choose__"}
								onValueChange={(value) => {
									if (value === "__global__") form.setData("isGlobal", true)
									else form.setData("isGlobal", false)
								}}
								disabled={isEdit}
							>
								<SelectTrigger>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									{scopes
										.filter((s) => s.canEdit)
										.map((s) => (
											<SelectItem key={s.teamSlug} value={s.isGlobal ? "__global__" : (s.teamId ?? s.teamSlug)}>
												{s.teamName}
											</SelectItem>
										))}
								</SelectContent>
							</Select>
							{!isOperatorEditor && <p className="text-xs text-muted-foreground">Only operators can edit the global default.</p>}
						</div>
						<div className="space-y-1">
							<Label>Scope</Label>
							<Select value={form.data.scope} onValueChange={(value) => form.setData("scope", value as ScopeKey)} disabled={isEdit}>
								<SelectTrigger>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="MARKET">Specific market</SelectItem>
									<SelectItem value="PROTOCOL">All markets for a protocol</SelectItem>
								</SelectContent>
							</Select>
						</div>
					</div>

					{form.data.scope === "MARKET" ? (
						<div className="space-y-1">
							<Label>Target market</Label>
							<Select value={form.data.targetMarketKey} onValueChange={(value) => form.setData("targetMarketKey", value)}>
								<SelectTrigger>
									<SelectValue placeholder="Pick a market" />
								</SelectTrigger>
								<SelectContent>
									{markets.map((m) => (
										<SelectItem key={marketKey(m)} value={marketKey(m)}>
											{m.label} · {m.protocol} · {formatChainId(m.chainId)}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
							<InputError message={form.errors.targetMarketKey} />
						</div>
					) : (
						<div className="space-y-1">
							<Label>Target protocol</Label>
							<Select value={form.data.targetProtocol} onValueChange={(value) => form.setData("targetProtocol", value)}>
								<SelectTrigger>
									<SelectValue placeholder="Pick a protocol" />
								</SelectTrigger>
								<SelectContent>
									{protocols.map((p) => (
										<SelectItem key={p} value={p}>
											{p}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
							<InputError message={form.errors.targetProtocol} />
						</div>
					)}
				</CardContent>
			</Card>

			<Card>
				<CardHeader>
					<div className="flex items-center justify-between">
						<div>
							<CardTitle>Weight overrides</CardTitle>
							<CardDescription>Default weight is 1.0. Only list combinations you want to change.</CardDescription>
						</div>
						<Button type="button" variant="outline" size="sm" onClick={addEntry}>
							Add weight
						</Button>
					</div>
				</CardHeader>
				<CardContent>
					{form.data.entries.length === 0 ? (
						<p className="text-sm text-muted-foreground">No overrides — every (category, sub_category) keeps the default weight of 1.0.</p>
					) : (
						<div className="space-y-3">
							{form.data.entries.map((entry, idx) => (
								<EntryRow
									key={entry.id ?? `new-${idx}`}
									entry={entry}
									protocol={targetProtocol}
									marketConfigId={selectedMarket?.marketConfigId ?? null}
									chainId={selectedMarket?.chainId ?? null}
									marketIdHex={selectedMarket?.marketIdHex ?? null}
									onChange={(field, value) => updateEntry(idx, field, value)}
									onRemove={() => removeEntry(idx)}
								/>
							))}
						</div>
					)}
				</CardContent>
			</Card>

			<div className="flex items-center justify-between">
				{isEdit ? (
					<Button type="button" variant="destructive" onClick={onDelete}>
						Delete profile
					</Button>
				) : (
					<span />
				)}
				<Button type="submit" disabled={form.processing}>
					{isEdit ? "Save changes" : "Create profile"}
				</Button>
			</div>
		</form>
	)
}

// ---------------------------------------------------------------------------
// Entry row with cascading sub-category dropdown
// ---------------------------------------------------------------------------

interface EntryRowProps {
	entry: WeightingProfileEntryInput
	protocol: string | null
	marketConfigId: string | null
	chainId: number | null
	marketIdHex: string | null
	onChange: (field: keyof WeightingProfileEntryInput, value: string) => void
	onRemove: () => void
}

function EntryRow({ entry, protocol, marketConfigId, chainId, marketIdHex, onChange, onRemove }: EntryRowProps) {
	const [options, setOptions] = useState<string[]>([])
	const [loading, setLoading] = useState(false)

	useEffect(() => {
		// Sub-categories depend on (category, protocol, market_config_id + chain + market).
		// Don't fire if we don't have enough context yet.
		if (!protocol && !marketConfigId) {
			setOptions([])
			return
		}
		// The API expects the enum value verbatim (uppercase, e.g. "ANCHOR").
		// Lowercasing it makes Litestar reject the query param with a 400,
		// which the fetch below swallows into an empty option list.
		const params = new URLSearchParams({ category: entry.category })
		if (protocol) params.set("protocol", protocol)
		if (marketConfigId) params.set("market_config_id", marketConfigId)
		if (chainId !== null) params.set("chain_id", String(chainId))
		if (marketIdHex) params.set("market_id_hex", marketIdHex)
		const controller = new AbortController()
		setLoading(true)
		fetch(`/api/weighting-profiles/available-sub-categories?${params}`, {
			signal: controller.signal,
			headers: { Accept: "application/json" },
		})
			.then((r) => (r.ok ? r.json() : { subCategories: [] }))
			.then((body) => setOptions(body.subCategories ?? body.sub_categories ?? []))
			.catch(() => setOptions([]))
			.finally(() => setLoading(false))
		return () => controller.abort()
	}, [entry.category, protocol, marketConfigId, chainId, marketIdHex])

	const optionsWithCurrent = useMemo(() => {
		// Always include the currently-selected sub_category so an existing
		// override doesn't disappear if the upstream snapshot temporarily
		// stops emitting that key.
		if (entry.subCategory && !options.includes(entry.subCategory)) {
			return [entry.subCategory, ...options]
		}
		return options
	}, [entry.subCategory, options])

	return (
		<div className="grid grid-cols-1 md:grid-cols-[150px_minmax(0,1fr)_120px_auto] items-end gap-3">
			<div className="space-y-1">
				<Label className="text-xs">Category</Label>
				<Select value={entry.category} onValueChange={(value) => onChange("category", value)}>
					<SelectTrigger>
						<SelectValue />
					</SelectTrigger>
					<SelectContent>
						{CATEGORY_OPTIONS.map((c) => (
							<SelectItem key={c.value} value={c.value}>
								{c.label}
							</SelectItem>
						))}
					</SelectContent>
				</Select>
			</div>
			<div className="space-y-1">
				<Label className="text-xs">Sub-category</Label>
				<Select value={entry.subCategory} onValueChange={(value) => onChange("subCategory", value)}>
					<SelectTrigger>
						<SelectValue placeholder={loading ? "Loading…" : "Pick a sub-category"} />
					</SelectTrigger>
					<SelectContent>
						{optionsWithCurrent.length === 0 ? (
							<div className="px-2 py-1 text-xs text-muted-foreground">No sub-categories detected yet</div>
						) : (
							optionsWithCurrent.map((sub) => (
								<SelectItem key={sub} value={sub}>
									{sub}
								</SelectItem>
							))
						)}
					</SelectContent>
				</Select>
			</div>
			<div className="space-y-1">
				<Label className="text-xs">Weight</Label>
				<Input type="number" step="0.01" min="0" value={entry.weight} onChange={(e) => onChange("weight", e.target.value)} />
			</div>
			<Button type="button" variant="ghost" size="icon" onClick={onRemove} aria-label="Remove entry">
				<Trash2 className="h-4 w-4" />
			</Button>
		</div>
	)
}
