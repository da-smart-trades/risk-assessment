// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { router, useForm } from "@inertiajs/react"
import { InputError } from "@/components/input-error"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { route } from "@/lib/generated/routes"

const _NONE = "none"

export interface MarketConfigInitial {
	id?: string
	protocol: string
	enabled: boolean
	assuranceProtocol?: string | null
}

interface Props {
	mode: "create" | "edit"
	initial: MarketConfigInitial
	protocolOptions: string[]
}

/**
 * Shared form for creating / editing a market_config row.
 *
 * Operators register *protocols* — the collector/scorer workflow
 * runs ``yarn <protocol>`` on every tick and fans out across the
 * markets that returns. ``protocol`` is the natural key and is
 * read-only on edit; toggling ``enabled`` is the only mutation.
 */
export function MarketConfigForm({ mode, initial, protocolOptions }: Props) {
	const form = useForm({
		protocol: initial.protocol,
		enabled: initial.enabled,
		assuranceProtocol: initial.assuranceProtocol ?? "",
	})

	const submit = (event: React.FormEvent) => {
		event.preventDefault()
		form.transform((data) => ({ ...data, assuranceProtocol: data.assuranceProtocol || null }))
		if (mode === "create") {
			form.post(route("admin.market_config.create"))
		} else if (initial.id) {
			form.patch(route("admin.market_config.update", { market_config_id: initial.id }))
		}
	}

	const onDelete = () => {
		if (mode !== "edit" || !initial.id) return
		if (!confirm("Delete this protocol? Snapshots, scores, and market favorites tied to it are cascade-deleted.")) {
			return
		}
		router.delete(route("admin.market_config.delete", { market_config_id: initial.id }), {
			preserveScroll: true,
		})
	}

	const isEdit = mode === "edit"

	return (
		<form onSubmit={submit} className="space-y-6">
			<Card>
				<CardHeader>
					<CardTitle>{isEdit ? "Edit protocol" : "Add a protocol"}</CardTitle>
					<CardDescription>
						Operators register the <strong>protocol</strong> only. The collector and scorer workers run <code>yarn &lt;protocol&gt;</code> on every tick to discover the live{" "}
						<code>(chainId, marketId, label)</code> set and produce one snapshot/score row per market. Markets show up on the markets list as soon as the first collector tick
						lands.
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					<div className="space-y-1">
						<Label htmlFor="protocol">Protocol</Label>
						<Input id="protocol" value={form.data.protocol} onChange={(e) => form.setData("protocol", e.target.value)} disabled={isEdit} placeholder="maple" required />
						<p className="text-xs text-muted-foreground">
							Lower-snake-case. Must match a script in <code>lending-markets-rating/package.json</code>.
						</p>
						<InputError message={form.errors.protocol} />
					</div>
					<div className="flex items-center gap-2">
						<Checkbox id="enabled" checked={form.data.enabled} onCheckedChange={(checked) => form.setData("enabled", checked === true)} />
						<Label htmlFor="enabled" className="cursor-pointer">
							Enabled — collector and scorer will tick this protocol
						</Label>
					</div>
					<div className="space-y-1">
						<Label htmlFor="assurance-protocol">Assurance metrics protocol</Label>
						<Select value={form.data.assuranceProtocol || _NONE} onValueChange={(v) => form.setData("assuranceProtocol", v === _NONE ? "" : v)}>
							<SelectTrigger id="assurance-protocol" className="max-w-xs">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value={_NONE}>None — no ASSURANCE metrics</SelectItem>
								{protocolOptions.map((p) => (
									<SelectItem key={p} value={p}>
										{p}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
						<p className="text-xs text-muted-foreground">
							Maps this yarn protocol to the ProtocolType whose ASSURANCE manual metrics apply when scoring its markets. Leave as <strong>None</strong> if the protocol has no
							assurance metrics.
						</p>
						<InputError message={form.errors.assuranceProtocol} />
					</div>
				</CardContent>
			</Card>

			<div className="flex items-center justify-between">
				{isEdit ? (
					<Button type="button" variant="destructive" onClick={onDelete}>
						Delete protocol
					</Button>
				) : (
					<span />
				)}
				<div className="flex items-center gap-2">
					<Button type="submit" disabled={form.processing}>
						{isEdit ? "Save changes" : "Add protocol"}
					</Button>
				</div>
			</div>
		</form>
	)
}
