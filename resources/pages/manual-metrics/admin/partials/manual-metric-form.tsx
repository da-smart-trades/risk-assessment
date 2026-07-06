// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { router, useForm } from "@inertiajs/react"
import { useEffect, useMemo, useState } from "react"
import { InputError } from "@/components/input-error"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { route } from "@/lib/generated/routes"

// ---------------------------------------------------------------------------
// Entity-type and category catalog
//
// Mirrors the server-side mapping in
// ``src/cert_ra/api/domain/manual_metrics/services.py::ALLOWED_CATEGORIES_BY_ENTITY``.
// "Market" is a UI-only entity type: it is sugar over a protocol-scoped
// ANCHORS metric pinned to one discovered market — the server stores it as
// ``protocol`` + ``market_chain_id`` / ``market_id_hex``.
// Keep in sync when the server-side allowlist changes.
// ---------------------------------------------------------------------------

const ENTITY_TYPES = ["chain", "token", "protocol", "market"] as const
type EntityType = (typeof ENTITY_TYPES)[number]
// Entity types backed by a single enum column (everything but "market").
type ColumnEntityType = Exclude<EntityType, "market">

const ENTITY_LABELS: Record<EntityType, string> = {
	chain: "Chain",
	token: "Token",
	protocol: "Protocol",
	market: "Market",
}

const ENTITY_VALUES: Record<ColumnEntityType, readonly string[]> = {
	chain: ["ARBITRUM", "ETHEREUM", "SOLANA", "BASE", "INK", "UNICHAIN", "POLYGON", "AVALANCHE_C", "OPTIMISM"],
	token: ["WETH", "USDE", "AAVE", "UNI", "USDC", "USDT0", "AUSDC", "CUSDC", "LINK", "STETH", "WSTETH"],
	protocol: ["AAVE_V3", "MORPHO_V2", "COMPOUND_V3", "DRIFT_V2"],
}

const ALLOWED_CATEGORIES: Record<EntityType, readonly string[]> = {
	chain: ["GOVERNANCE"],
	token: ["ANCHORS", "CONTROL", "ASSURANCE", "TOKEN_RISK"],
	protocol: ["ANCHORS", "CONTROL", "ASSURANCE", "PROTOCOL_SCORE"],
	// A market metric is always an anchor (the only manual category that
	// feeds a market's Probability of Default).
	market: ["ANCHORS"],
}

const RESERVED_CATEGORIES = new Set(["PROTOCOL_SCORE", "TOKEN_RISK"])

const DEFAULT_ENTITY_TYPE: EntityType = "protocol"

/** One discovered market as returned by `/api/markets/alert-options`. */
interface DiscoveredMarket {
	marketConfigId: string
	protocol: string
	chainId: number
	marketIdHex: string
	label: string
	// The ProtocolType (e.g. "AAVE_V3") this market maps to for manual metrics.
	assuranceProtocol: string | null
}

/** Build the key identifying one market within the picker. */
function pinKey(chainId: number | string, marketIdHex: string): string {
	return `${chainId}:${marketIdHex}`
}

/** A short, human label for a market pin on the edit (read-only) view. */
function shortHex(hex: string): string {
	return hex.length > 14 ? `${hex.slice(0, 8)}…${hex.slice(-4)}` : hex
}

// ---------------------------------------------------------------------------

export interface ManualMetricFormInitial {
	id?: string
	name: string
	desc: string
	category: string
	chain?: string | null
	token?: string | null
	protocol?: string | null
	subCategory?: string | null
	value?: string | null
	riskScore?: number | null
	notes?: string | null
	marketChainId?: number | null
	marketIdHex?: string | null
	teamId?: string | null
	teamName?: string | null
	isPublished?: boolean
}

interface Props {
	initial?: ManualMetricFormInitial
	// True when the current user is allowed to create reserved-category
	// metrics (PROTOCOL_SCORE, TOKEN_RISK). Drives whether those options are
	// shown in the category dropdown.
	isOperatorEditor: boolean
	// Display-only label for the resulting scope ("Shared (platform-wide)"
	// for operator editors; the team name for team editors). Computed by the
	// page wrapper, since scope is server-derived.
	scopeLabel: string
}

function deriveEntityType(initial?: ManualMetricFormInitial): EntityType {
	// A market-pinned row is presented as the "market" entity even though
	// the server stores it under the protocol column.
	if (initial?.marketChainId != null && initial?.marketIdHex) return "market"
	if (initial?.chain) return "chain"
	if (initial?.token) return "token"
	if (initial?.protocol) return "protocol"
	return DEFAULT_ENTITY_TYPE
}

function deriveEntityValue(initial: ManualMetricFormInitial | undefined, entityType: EntityType): string {
	if (entityType === "market") {
		if (initial?.marketChainId != null && initial?.marketIdHex) return pinKey(initial.marketChainId, initial.marketIdHex)
		return ""
	}
	if (!initial) return ENTITY_VALUES[entityType][0] ?? ""
	const raw = (initial[entityType] ?? "") as string
	return raw || (ENTITY_VALUES[entityType][0] ?? "")
}

function deriveCategory(initial: ManualMetricFormInitial | undefined, entityType: EntityType, isOperatorEditor: boolean): string {
	const available = ALLOWED_CATEGORIES[entityType].filter((c) => isOperatorEditor || !RESERVED_CATEGORIES.has(c))
	if (initial?.category && available.includes(initial.category)) {
		return initial.category
	}
	return available[0] ?? ALLOWED_CATEGORIES[entityType][0]
}

export default function ManualMetricForm({ initial, isOperatorEditor, scopeLabel }: Props) {
	const isEdit = Boolean(initial?.id)
	const initialEntityType = deriveEntityType(initial)
	const initialEntityValue = deriveEntityValue(initial, initialEntityType)
	const initialCategory = deriveCategory(initial, initialEntityType, isOperatorEditor)

	const { data, setData, post, patch, processing, errors, transform } = useForm({
		name: initial?.name ?? "",
		desc: initial?.desc ?? "",
		entityType: initialEntityType,
		// For "market" this holds the pin key (chainId:hex); for the other
		// entity types it holds the enum value.
		entityValue: initialEntityValue,
		category: initialCategory,
		subCategory: initial?.subCategory ?? "",
		value: initial?.value ?? "",
		riskScore: initial?.riskScore == null ? "" : String(initial.riskScore),
		notes: initial?.notes ?? "",
	})

	const effectiveScope = isEdit ? (initial?.teamId ? (initial?.teamName ?? "Team") : "Shared (platform-wide)") : scopeLabel

	const isMarket = data.entityType === "market"

	// Discovered markets, lazy-loaded the first time the market picker shows.
	const [markets, setMarkets] = useState<DiscoveredMarket[]>([])
	useEffect(() => {
		if (isEdit || !isMarket || markets.length > 0) return
		;(async () => {
			try {
				const response = await fetch("/api/markets/alert-options")
				if (!response.ok) return
				const body = await response.json()
				if (Array.isArray(body.items)) setMarkets(body.items)
			} catch {
				// Silent — an empty picker surfaces the failure to the user.
			}
		})()
	}, [isEdit, isMarket, markets.length])

	// Only markets that map to a ProtocolType can host a manual anchor (that
	// mapping is what links the metric to the market's PD).
	const marketOptions = useMemo(() => markets.filter((m) => m.assuranceProtocol), [markets])

	// Category options visible to this user for the current entity type.
	const categoryOptions = ALLOWED_CATEGORIES[data.entityType].filter((c) => isOperatorEditor || !RESERVED_CATEGORIES.has(c))

	const onEntityTypeChange = (next: EntityType) => {
		setData("entityType", next)
		if (next === "market") {
			// The user must pick a specific market; category is fixed to ANCHORS.
			setData("entityValue", "")
			setData("category", "ANCHORS")
			return
		}
		setData("entityValue", ENTITY_VALUES[next][0] ?? "")
		const available = ALLOWED_CATEGORIES[next].filter((c) => isOperatorEditor || !RESERVED_CATEGORIES.has(c))
		setData("category", available[0] ?? ALLOWED_CATEGORIES[next][0])
	}

	const submit = (e: React.FormEvent) => {
		e.preventDefault()

		transform((d) => {
			// On edit the server ignores entity / category / pin fields (they're
			// immutable), but we still send the mutable content fields.
			const base = {
				name: d.name,
				desc: d.desc,
				subCategory: d.subCategory || null,
				value: d.value || null,
				riskScore: d.riskScore === "" ? null : Number(d.riskScore),
				notes: d.notes || null,
			}
			if (isEdit) return base

			if (d.entityType === "market") {
				// "Market" is sugar: store as a protocol-scoped ANCHORS metric
				// pinned to the chosen market.
				const m = markets.find((mk) => pinKey(mk.chainId, mk.marketIdHex) === d.entityValue)
				return {
					...base,
					chain: null,
					token: null,
					protocol: m?.assuranceProtocol ?? null,
					category: "ANCHORS",
					marketChainId: m?.chainId ?? null,
					marketIdHex: m?.marketIdHex ?? null,
				}
			}

			const entityPayload: Record<string, string | null> = {
				chain: null,
				token: null,
				protocol: null,
			}
			entityPayload[d.entityType] = d.entityValue
			return {
				...base,
				...entityPayload,
				category: d.category,
				marketChainId: null,
				marketIdHex: null,
			}
		})

		if (isEdit && initial?.id) {
			patch(route("manual_metrics.admin.update", { metric_id: initial.id }), {
				onSuccess: () => {
					toast({
						title: "Manual metric updated",
						description: `Updated "${data.name}".`,
						variant: "success",
					})
				},
			})
			return
		}

		post(route("manual_metrics.admin.create"), {
			onSuccess: () => {
				toast({
					title: "Manual metric created",
					description: `Created "${data.name}" as a draft. Publish it to make it visible.`,
					variant: "success",
				})
			},
		})
	}

	const handlePublishToggle = () => {
		if (!initial?.id) return
		const next = !initial.isPublished
		router.patch(
			route("manual_metrics.admin.publish", { metric_id: initial.id }),
			{ isPublished: next },
			{
				preserveScroll: true,
				onSuccess: () => {
					toast({
						title: next ? "Published" : "Unpublished",
						description: next ? `"${data.name}" is now visible.` : `"${data.name}" is no longer visible to readers.`,
						variant: "success",
					})
				},
			},
		)
	}

	// Block submit on a market metric with no market chosen yet.
	const marketUnselected = !isEdit && isMarket && !data.entityValue
	const marketEditLabel = initial?.protocol ? `${initial.protocol} · chain ${initial.marketChainId} · ${initial.marketIdHex ? shortHex(initial.marketIdHex) : ""}` : "—"

	return (
		<Card>
			<CardHeader>
				<CardTitle>{isEdit ? "Edit manual metric" : "New manual metric"}</CardTitle>
				<CardDescription>
					{isEdit ? (
						<>Entity, scope, and category are immutable after creation. Edit the content fields below.</>
					) : (
						<>
							Pick an entity, then a category valid for that entity. A <strong>Market</strong> metric is an anchor pinned to one market and feeds that market's Probability of
							Default. The metric is created in <strong>draft</strong> state and must be published before it becomes visible.
						</>
					)}
				</CardDescription>
			</CardHeader>
			<CardContent>
				<form onSubmit={submit} className="space-y-6">
					<div className="flex flex-wrap items-end gap-4">
						<div>
							<Label>Scope</Label>
							<div className="mt-1 rounded-md border bg-muted/30 px-3 py-2 text-sm">{effectiveScope}</div>
						</div>
						{isEdit && (
							<div>
								<Label>State</Label>
								<div className="mt-1 flex items-center gap-2">
									{initial?.isPublished ? <Badge variant="default">Published</Badge> : <Badge variant="secondary">Draft</Badge>}
									<Button type="button" size="sm" variant="outline" onClick={handlePublishToggle}>
										{initial?.isPublished ? "Unpublish" : "Publish"}
									</Button>
								</div>
							</div>
						)}
					</div>

					<div className="grid gap-4 sm:grid-cols-3">
						<div>
							<Label htmlFor="entityType">Entity type</Label>
							{isEdit ? (
								<div className="mt-1 rounded-md border bg-muted/30 px-3 py-2 text-sm">{ENTITY_LABELS[data.entityType]}</div>
							) : (
								<Select value={data.entityType} onValueChange={(v) => onEntityTypeChange(v as EntityType)}>
									<SelectTrigger id="entityType" className="mt-1">
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										{ENTITY_TYPES.map((t) => (
											<SelectItem key={t} value={t}>
												{ENTITY_LABELS[t]}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							)}
							<InputError message={errors.entityType} className="mt-2" />
						</div>

						<div>
							<Label htmlFor="entityValue">{ENTITY_LABELS[data.entityType]}</Label>
							{isEdit ? (
								<div className="mt-1 rounded-md border bg-muted/30 px-3 py-2 text-sm">{isMarket ? marketEditLabel : data.entityValue}</div>
							) : isMarket ? (
								<>
									<Select value={data.entityValue} onValueChange={(v) => setData("entityValue", v)}>
										<SelectTrigger id="entityValue" className="mt-1">
											<SelectValue placeholder="Select a market…" />
										</SelectTrigger>
										<SelectContent>
											{marketOptions.map((m) => (
												<SelectItem key={pinKey(m.chainId, m.marketIdHex)} value={pinKey(m.chainId, m.marketIdHex)}>
													{m.label} ({m.assuranceProtocol} · chain {m.chainId})
												</SelectItem>
											))}
										</SelectContent>
									</Select>
									{marketOptions.length === 0 && (
										<p className="mt-1 text-muted-foreground text-xs">
											No discovered markets are mapped to a protocol yet. Set a market's assurance protocol under Admin → Market config first.
										</p>
									)}
								</>
							) : (
								<Select value={data.entityValue} onValueChange={(v) => setData("entityValue", v)}>
									<SelectTrigger id="entityValue" className="mt-1">
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										{ENTITY_VALUES[data.entityType].map((v) => (
											<SelectItem key={v} value={v}>
												{v}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							)}
							<InputError message={errors.entityValue} className="mt-2" />
						</div>

						<div>
							<Label htmlFor="category">Category</Label>
							{isEdit || isMarket ? (
								<div className="mt-1 rounded-md border bg-muted/30 px-3 py-2 text-sm">{isMarket ? "ANCHORS" : data.category}</div>
							) : (
								<>
									<Select value={data.category} onValueChange={(v) => setData("category", v)}>
										<SelectTrigger id="category" className="mt-1">
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											{categoryOptions.map((c) => (
												<SelectItem key={c} value={c}>
													{c}
												</SelectItem>
											))}
										</SelectContent>
									</Select>
									{categoryOptions.length < ALLOWED_CATEGORIES[data.entityType].length && (
										<p className="mt-1 text-muted-foreground text-xs">Reserved categories (PROTOCOL_SCORE, TOKEN_RISK) require operator-team editor permissions.</p>
									)}
								</>
							)}
							<InputError message={errors.category} className="mt-2" />
						</div>
					</div>

					<div>
						<Label htmlFor="name">Name</Label>
						<Input id="name" value={data.name} onChange={(e) => setData("name", e.target.value)} className="mt-1" required />
						<InputError message={errors.name} className="mt-2" />
					</div>

					<div>
						<Label htmlFor="desc">Description</Label>
						<Textarea id="desc" value={data.desc} onChange={(e) => setData("desc", e.target.value)} className="mt-1" rows={3} required />
						<InputError message={errors.desc} className="mt-2" />
					</div>

					<div className="grid gap-4 sm:grid-cols-2">
						<div>
							<Label htmlFor="subCategory">Sub-category (optional)</Label>
							<Input id="subCategory" value={data.subCategory} onChange={(e) => setData("subCategory", e.target.value)} className="mt-1" />
							<InputError message={errors.subCategory} className="mt-2" />
						</div>

						<div>
							<Label htmlFor="riskScore">Risk score (1–5, optional)</Label>
							<Input id="riskScore" type="number" min={1} max={5} value={data.riskScore} onChange={(e) => setData("riskScore", e.target.value)} className="mt-1" />
							<p className="mt-1 text-muted-foreground text-xs">1 = lowest risk · 5 = highest risk</p>
							<InputError message={errors.riskScore} className="mt-2" />
						</div>

						<div>
							<Label htmlFor="value">{data.category === "ANCHORS" ? "Value — probability of default (0–1)" : "Value (optional)"}</Label>
							<Input
								id="value"
								value={data.value}
								onChange={(e) => setData("value", e.target.value)}
								className="mt-1"
								placeholder={data.category === "ANCHORS" ? "e.g. 0.2" : "e.g. 0.92, passing, see report §3"}
							/>
							{data.category === "ANCHORS" && (
								<p className="mt-1 text-muted-foreground text-xs">Folds into the market's Probability of Default as an anchor. Must be a probability in [0, 1).</p>
							)}
							<InputError message={errors.value} className="mt-2" />
						</div>
					</div>

					<div>
						<Label htmlFor="notes">Notes (optional)</Label>
						<Textarea id="notes" value={data.notes} onChange={(e) => setData("notes", e.target.value)} className="mt-1" rows={3} />
						<InputError message={errors.notes} className="mt-2" />
					</div>

					<Button type="submit" disabled={processing || marketUnselected}>
						{isEdit ? "Save changes" : "Create draft"}
					</Button>
				</form>
			</CardContent>
		</Card>
	)
}
