// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { ChevronDown } from "lucide-react"
import type { ReactNode } from "react"
import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { formatEvidenceKey } from "@/lib/format-evidence"

interface EvidenceTreeProps {
	/**
	 * Arbitrary JSON dict produced by the yarn collector. Keys are
	 * camelCase metric names; values are strings, numbers, arrays of
	 * either, or (rarely) nested objects.
	 */
	evidence: Record<string, unknown> | null | undefined
	/**
	 * Optional message rendered when `evidence` is null / empty.
	 * Defaults to "No evidence yet for this market." which matches the
	 * pre-tick state.
	 */
	emptyMessage?: string
}

/**
 * Render the latest evidence JSON for a market.
 *
 * Each top-level key becomes a row: the label is the camelCase key
 * converted to Title Case via `formatEvidenceKey`, and the value is
 * dispatched on its runtime type via `<EvidenceValue>`.
 *
 * Nested objects render as a collapsible block that reuses the same
 * key/value layout, so arbitrarily deep evidence trees expand cleanly.
 * Arrays of objects expand the same way (each row inside the array
 * collapsible is itself a `<EvidenceValue>`).
 */
export function EvidenceTree({ evidence, emptyMessage = "No evidence yet for this market." }: EvidenceTreeProps) {
	if (!evidence || Object.keys(evidence).length === 0) {
		return <p className="text-sm text-muted-foreground">{emptyMessage}</p>
	}
	return (
		<dl className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-x-6 gap-y-3">
			{Object.entries(evidence).map(([key, value]) => (
				<EvidenceRow key={key} label={formatEvidenceKey(key)} value={value} />
			))}
		</dl>
	)
}

function EvidenceRow({ label, value }: { label: string; value: unknown }) {
	return (
		<>
			<dt className="text-sm font-medium text-foreground">{label}</dt>
			<dd className="text-sm text-foreground/90 min-w-0">
				<EvidenceValue value={value} />
			</dd>
		</>
	)
}

function EvidenceValue({ value }: { value: unknown }): ReactNode {
	if (value === null || value === undefined) {
		return <span className="text-muted-foreground">—</span>
	}
	if (typeof value === "string") {
		return <span className="whitespace-pre-wrap break-words">{value}</span>
	}
	if (typeof value === "number") {
		return <span className="tabular-nums">{value.toLocaleString()}</span>
	}
	if (typeof value === "boolean") {
		return <span>{value ? "yes" : "no"}</span>
	}
	if (Array.isArray(value)) {
		return <CollapsibleArray items={value} />
	}
	if (typeof value === "object") {
		return <CollapsibleObject record={value as Record<string, unknown>} />
	}
	return <span>{String(value)}</span>
}

function CollapsibleObject({ record }: { record: Record<string, unknown> }) {
	const entries = Object.entries(record)
	const [open, setOpen] = useState(false)
	if (entries.length === 0) {
		return <span className="text-muted-foreground">empty object</span>
	}
	return (
		<Collapsible open={open} onOpenChange={setOpen} className="w-full">
			<CollapsibleTrigger className="group inline-flex items-center gap-2 text-left cursor-pointer">
				<Badge variant="secondary" className="font-mono">
					{entries.length}
				</Badge>
				<span className="text-sm text-muted-foreground group-hover:text-foreground">
					{open ? "Hide" : "Show"} {entries.length === 1 ? "field" : "fields"}
				</span>
				<ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
			</CollapsibleTrigger>
			<CollapsibleContent className="mt-2">
				<dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-x-4 gap-y-2 border-l border-border pl-4">
					{entries.map(([key, val]) => (
						<EvidenceRow key={key} label={formatEvidenceKey(key)} value={val} />
					))}
				</dl>
			</CollapsibleContent>
		</Collapsible>
	)
}

function CollapsibleArray({ items }: { items: unknown[] }) {
	const [open, setOpen] = useState(false)
	if (items.length === 0) {
		return <span className="text-muted-foreground">empty list</span>
	}
	return (
		<Collapsible open={open} onOpenChange={setOpen} className="w-full">
			<CollapsibleTrigger className="group inline-flex items-center gap-2 text-left cursor-pointer">
				<Badge variant="secondary" className="font-mono">
					{items.length}
				</Badge>
				<span className="text-sm text-muted-foreground group-hover:text-foreground">
					{open ? "Hide" : "Show"} {items.length === 1 ? "item" : "items"}
				</span>
				<ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
			</CollapsibleTrigger>
			<CollapsibleContent className="mt-2">
				<ul className="list-disc pl-5 space-y-1">
					{items.map((item, i) => {
						// Evidence arrays are read-only and never reordered, so the
						// position-derived key is stable enough; including the stringified
						// value gives biome something other than the bare index.
						const key = `${i}-${typeof item === "string" ? item.slice(0, 32) : typeof item}`
						return (
							<li key={key} className="text-sm">
								<EvidenceValue value={item} />
							</li>
						)
					})}
				</ul>
			</CollapsibleContent>
		</Collapsible>
	)
}
