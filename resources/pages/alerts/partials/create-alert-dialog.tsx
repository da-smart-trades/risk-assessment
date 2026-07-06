// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { zodResolver } from "@hookform/resolvers/zod"
import { router } from "@inertiajs/react"
import { Plus } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"

const THRESHOLD_OPERATORS = [">", ">=", "<", "<=", "==", "!="] as const
const DIRECTIONS = ["above", "below", "both"] as const

const TARGET_KINDS = ["METRIC", "MARKET_PD", "MARKET_ANCHOR", "MARKET_CONTROL"] as const

const RULE_KINDS = ["THRESHOLD", "RATE_OF_CHANGE", "STDDEV_DEVIATION"] as const

const alertSchema = z
	.object({
		name: z.string().min(1, "Name is required"),
		description: z.string().min(1, "Description is required"),
		severity: z.enum(["INFO", "WARNING", "CRITICAL"]),
		isTemplate: z.boolean(),
		isEnabled: z.boolean(),

		// Target
		targetKind: z.enum(TARGET_KINDS),
		// METRIC target fields
		metricType: z.string().optional(),
		chain: z.string().optional(),
		token: z.string().optional(),
		// MARKET_* target fields
		marketSelection: z.string().optional(), // serialized "configId|chainId|hex|label|protocol"
		subCategory: z.string().optional(),

		// Rule
		ruleKind: z.enum(RULE_KINDS),
		// Threshold fields
		thresholdOperator: z.string().optional(),
		thresholdValue: z.coerce.number().optional(),
		thresholdWindowSeconds: z.coerce.number().min(0).optional(),
		// Rate-of-change fields
		rateOfChangeDeltaPct: z.coerce.number().min(0).optional(),
		rateOfChangeWindowSeconds: z.coerce.number().min(1).optional(),
		rateOfChangeDirection: z.enum(DIRECTIONS).optional(),
		// Stddev-deviation fields
		stddevMultiplier: z.coerce.number().positive().optional(),
		stddevLookbackSeconds: z.coerce.number().min(1).optional(),
		stddevDirection: z.enum(DIRECTIONS).optional(),
	})
	.superRefine((data, ctx) => {
		// Target validation
		if (data.targetKind === "METRIC") {
			if (!data.metricType) {
				ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Metric type is required", path: ["metricType"] })
			}
		} else {
			if (!data.marketSelection) {
				ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Select a market", path: ["marketSelection"] })
			}
			if ((data.targetKind === "MARKET_ANCHOR" || data.targetKind === "MARKET_CONTROL") && !data.subCategory) {
				ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Sub-category is required", path: ["subCategory"] })
			}
		}
		// Rule validation
		if (data.ruleKind === "THRESHOLD") {
			if (!data.thresholdOperator) {
				ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Operator is required", path: ["thresholdOperator"] })
			}
			if (data.thresholdValue === undefined || data.thresholdValue === null || Number.isNaN(data.thresholdValue)) {
				ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Value is required", path: ["thresholdValue"] })
			}
		} else if (data.ruleKind === "RATE_OF_CHANGE") {
			if (data.rateOfChangeDeltaPct === undefined || Number.isNaN(data.rateOfChangeDeltaPct)) {
				ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Delta % magnitude is required", path: ["rateOfChangeDeltaPct"] })
			}
			if (!data.rateOfChangeWindowSeconds) {
				ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Window (seconds) is required", path: ["rateOfChangeWindowSeconds"] })
			}
		} else if (data.ruleKind === "STDDEV_DEVIATION") {
			if (!data.stddevMultiplier) {
				ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Multiplier is required", path: ["stddevMultiplier"] })
			}
			if (!data.stddevLookbackSeconds) {
				ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Lookback (seconds) is required", path: ["stddevLookbackSeconds"] })
			}
		}
	})

type AlertFormValues = z.infer<typeof alertSchema>

const METRIC_TYPES = [
	"TVL",
	"NUMBER_OF_NODES",
	"NUMBER_OF_SOFTWARE_CLIENTS",
	"GAS_PRICE",
	"TRANSACTIONS_PER_SECOND",
	"BLOCKS_PER_SECOND",
	"DELAY_ON_UPGRADE",
	"EXIT_WINDOW",
	"STATE_VALIDATION",
	"UPGRADE_TRANSPARENCY",
	"SLASHING_BEHAVIOR",
	"LAST_RELEASE_DATE",
	"VITALIK_ROLLUP_MILESTONE",
	"TOTAL_AMOUNT_OF_STAKES",
	"NAKAMOTO_LIVENESS_COEFFICIENT",
	"NAKAMOTO_SAFETY_COEFFICIENT",
	"HHI",
	"RENYI_ENTROPY_ALPHA_0",
	"RENYI_ENTROPY_ALPHA_1",
	"RENYI_ENTROPY_ALPHA_2",
	"RENYI_ENTROPY_ALPHA_INF",
	"SHAPLEY_TOP_VALUE",
	"SHAPLEY_SECOND_VALUE",
	"SHAPLEY_THIRD_VALUE",
	"USDC_INFLOW",
	"USDC_OUTFLOW",
	"USDC_UNIQUE_ADDRESSES",
	"USDC_TRANSACTION_COUNT",
	"USDC_TOTAL_SUPPLY",
	"USDT0_TOTAL_AMOUNT_TRANSFERS",
	"USDT0_INFLOW",
	"USDT0_OUTFLOW",
	"USDT0_UNIQUE_ADDRESSES",
	"USDT0_TRANSACTION_COUNT",
	"USDT0_TVL",
	"ETH_FINALITY",
	"SOL_FINALITY",
	"ARB_FINALITY",
	"INK_FINALITY",
	"POLYGON_FINALITY",
	"UNICHAIN_FINALITY",
	"BASE_FINALITY",
	"ETH_WETH_INFLOW",
	"ETH_WETH_OUTFLOW",
	"ETH_WETH_TOTAL_SUPPLY",
	"ETH_USDE_TOTAL_SUPPLY",
	"ETH_USDE_TRANSFER_COUNT",
	"ETH_USDE_UNIQUE_ADDRESSES",
	"ETH_USDE_VOLUME",
	"ETH_AAVE_TOTAL_SUPPLY",
	"ETH_AAVE_TRANSFER_COUNT",
	"ETH_AAVE_UNIQUE_ADDRESSES",
	"ETH_AAVE_VOLUME",
	"ETH_UNI_TOTAL_SUPPLY",
	"ETH_UNI_TRANSFER_COUNT",
	"ETH_UNI_UNIQUE_ADDRESSES",
	"ETH_UNI_VOLUME",
	"UNIQUE_ADDRESSES",
	"TRANSACTION_COUNT",
	"VOTING_DELEGATION_LAYER_COEFFICIENT",
	"UPGRADE_AUTHORITY_COEFFICIENT",
	"EMERGENCY_POWERS_BYPASS_RISK",
	"BASE_GOVERNANCE_EXECUTION",
	"SOLANA_GOVERNANCE_PROPOSALS",
	"ETH_GOVERNANCE_PROPOSALS",
	"ARB_GOVERNANCE_PROPOSALS",
	"ARB_GOVERNANCE_EXECUTION",
	"ARB_GOVERNANCE_EMERGENCY",
	"DECENTRALIZATION_COMBINED",
	"TIME_TO_FINALITY_SOFT",
] as const

const CHAINS = ["ARBITRUM", "ETHEREUM", "SOLANA", "BASE", "INK", "UNICHAIN", "POLYGON", "AVALANCHE_C", "OPTIMISM"] as const
const TOKENS = ["WETH", "USDE", "AAVE", "UNI", "USDC", "USDT0", "AUSDC", "CUSDC", "LINK", "STETH", "WSTETH"] as const

interface MarketOption {
	marketConfigId: string
	protocol: string
	chainId: number
	marketIdHex: string
	label: string
}

function serializeMarket(option: MarketOption): string {
	return [option.marketConfigId, option.chainId, option.marketIdHex, option.label, option.protocol].join("|")
}

function deserializeMarket(value: string | undefined): MarketOption | null {
	if (!value) return null
	const parts = value.split("|")
	if (parts.length < 5) return null
	return {
		marketConfigId: parts[0],
		chainId: Number(parts[1]),
		marketIdHex: parts[2],
		label: parts[3],
		protocol: parts[4],
	}
}

interface CreateAlertDialogProps {
	isOperatorEditor: boolean
	isTeamEditor: boolean
}

export function CreateAlertDialog({ isOperatorEditor, isTeamEditor }: CreateAlertDialogProps) {
	const [open, setOpen] = useState(false)
	const [submitting, setSubmitting] = useState(false)
	const [markets, setMarkets] = useState<MarketOption[]>([])
	const [subCategories, setSubCategories] = useState<{ anchors: string[]; controlModifiers: string[] }>({ anchors: [], controlModifiers: [] })

	const form = useForm<AlertFormValues>({
		resolver: zodResolver(alertSchema),
		defaultValues: {
			name: "",
			description: "",
			severity: "WARNING",
			isTemplate: false,
			isEnabled: true,
			targetKind: "METRIC",
			metricType: undefined,
			chain: undefined,
			token: undefined,
			marketSelection: undefined,
			subCategory: undefined,
			ruleKind: "THRESHOLD",
			thresholdOperator: ">",
			thresholdValue: undefined,
			thresholdWindowSeconds: 0,
			rateOfChangeDeltaPct: undefined,
			rateOfChangeWindowSeconds: undefined,
			rateOfChangeDirection: "both",
			stddevMultiplier: 1,
			stddevLookbackSeconds: undefined,
			stddevDirection: "both",
		},
	})

	const targetKind = form.watch("targetKind")
	const ruleKind = form.watch("ruleKind")
	const marketSelection = form.watch("marketSelection")
	const selectedMarket = deserializeMarket(marketSelection)

	// Lazy-load discovered markets the first time the user opens the dialog and
	// switches to a market target. Avoids paying the cost on every page load.
	useEffect(() => {
		if (!open) return
		if (targetKind === "METRIC") return
		if (markets.length > 0) return
		;(async () => {
			try {
				const response = await fetch("/api/markets/alert-options")
				if (!response.ok) return
				const body = await response.json()
				if (Array.isArray(body.items)) setMarkets(body.items)
			} catch {
				// Silent — empty dropdown surfaces the failure to the user.
			}
		})()
	}, [open, targetKind, markets.length])

	// Pull sub_categories whenever the market changes for anchor / control rules.
	useEffect(() => {
		if (!open) return
		if (targetKind !== "MARKET_ANCHOR" && targetKind !== "MARKET_CONTROL") return
		if (!selectedMarket) return
		;(async () => {
			try {
				const response = await fetch(`/api/markets/${selectedMarket.marketConfigId}/${selectedMarket.chainId}/${selectedMarket.marketIdHex}/alert-sub-categories`)
				if (!response.ok) return
				const body = await response.json()
				setSubCategories({ anchors: body.anchors ?? [], controlModifiers: body.controlModifiers ?? [] })
			} catch {
				setSubCategories({ anchors: [], controlModifiers: [] })
			}
		})()
	}, [open, targetKind, selectedMarket])

	const onSubmit = form.handleSubmit((values) => {
		let targetConfig: Record<string, unknown> = {}
		if (values.targetKind === "METRIC") {
			targetConfig = {
				type: "METRIC",
				metricType: values.metricType,
				chain: values.chain || null,
				token: values.token || null,
			}
		} else {
			const market = deserializeMarket(values.marketSelection)
			if (!market) return
			targetConfig = {
				type: values.targetKind,
				marketConfigId: market.marketConfigId,
				chainId: market.chainId,
				marketIdHex: market.marketIdHex,
				...(values.targetKind !== "MARKET_PD" ? { subCategory: values.subCategory } : {}),
			}
		}

		let ruleConfig: Record<string, unknown> = {}
		if (values.ruleKind === "THRESHOLD") {
			ruleConfig = {
				type: "THRESHOLD",
				operator: values.thresholdOperator,
				value: values.thresholdValue,
				windowSeconds: values.thresholdWindowSeconds ?? 0,
			}
		} else if (values.ruleKind === "RATE_OF_CHANGE") {
			ruleConfig = {
				type: "RATE_OF_CHANGE",
				deltaPct: values.rateOfChangeDeltaPct,
				windowSeconds: values.rateOfChangeWindowSeconds,
				direction: values.rateOfChangeDirection ?? "both",
			}
		} else if (values.ruleKind === "STDDEV_DEVIATION") {
			ruleConfig = {
				type: "STDDEV_DEVIATION",
				multiplier: values.stddevMultiplier,
				lookbackSeconds: values.stddevLookbackSeconds,
				direction: values.stddevDirection ?? "both",
			}
		}

		setSubmitting(true)
		router.post(
			"/alerts",
			{
				name: values.name,
				description: values.description,
				severity: values.severity,
				isTemplate: values.isTemplate,
				isEnabled: values.isEnabled,
				targetKind: values.targetKind,
				targetConfig,
				ruleKind: values.ruleKind,
				ruleConfig,
				integrationIds: [],
			},
			{
				onSuccess: () => {
					setOpen(false)
					form.reset()
				},
				onError: (errors) => {
					for (const [_field, message] of Object.entries(errors)) {
						if (typeof message === "string") {
							form.setError("root", { message })
						}
					}
				},
				onFinish: () => setSubmitting(false),
			},
		)
	})

	const canOpen = isOperatorEditor || isTeamEditor
	if (!canOpen) return null

	const subCategoryOptions = targetKind === "MARKET_ANCHOR" ? subCategories.anchors : targetKind === "MARKET_CONTROL" ? subCategories.controlModifiers : []

	return (
		<Dialog open={open} onOpenChange={setOpen}>
			<DialogTrigger asChild>
				<Button size="sm">
					<Plus className="mr-2 h-4 w-4" />
					Add Alert
				</Button>
			</DialogTrigger>
			<DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
				<DialogHeader>
					<DialogTitle>Create Alert</DialogTitle>
				</DialogHeader>

				<Form {...form}>
					<form onSubmit={onSubmit} className="space-y-4">
						{form.formState.errors.root && <p className="text-destructive text-sm">{form.formState.errors.root.message}</p>}

						<FormField
							control={form.control}
							name="name"
							render={({ field }) => (
								<FormItem>
									<FormLabel>Name</FormLabel>
									<FormControl>
										<Input placeholder="Alert name" {...field} />
									</FormControl>
									<FormMessage />
								</FormItem>
							)}
						/>

						<FormField
							control={form.control}
							name="description"
							render={({ field }) => (
								<FormItem>
									<FormLabel>Description</FormLabel>
									<FormControl>
										<Textarea placeholder="Describe what this alert monitors…" rows={2} {...field} />
									</FormControl>
									<FormMessage />
								</FormItem>
							)}
						/>

						<FormField
							control={form.control}
							name="targetKind"
							render={({ field }) => (
								<FormItem>
									<FormLabel>What does this alert monitor?</FormLabel>
									<Select onValueChange={field.onChange} value={field.value}>
										<FormControl>
											<SelectTrigger>
												<SelectValue />
											</SelectTrigger>
										</FormControl>
										<SelectContent>
											<SelectItem value="METRIC">Blockchain metric</SelectItem>
											<SelectItem value="MARKET_PD">Market — Probability of Default</SelectItem>
											<SelectItem value="MARKET_ANCHOR">Market — anchor PD</SelectItem>
											<SelectItem value="MARKET_CONTROL">Market — control modifier</SelectItem>
										</SelectContent>
									</Select>
									<FormMessage />
								</FormItem>
							)}
						/>

						{targetKind === "METRIC" && (
							<>
								<FormField
									control={form.control}
									name="metricType"
									render={({ field }) => (
										<FormItem>
											<FormLabel>Metric Type</FormLabel>
											<Select onValueChange={field.onChange} value={field.value}>
												<FormControl>
													<SelectTrigger>
														<SelectValue placeholder="Select a metric" />
													</SelectTrigger>
												</FormControl>
												<SelectContent className="max-h-64">
													{METRIC_TYPES.map((m) => (
														<SelectItem key={m} value={m}>
															{m}
														</SelectItem>
													))}
												</SelectContent>
											</Select>
											<FormMessage />
										</FormItem>
									)}
								/>
								<div className="grid grid-cols-2 gap-3">
									<FormField
										control={form.control}
										name="chain"
										render={({ field }) => (
											<FormItem>
												<FormLabel>Chain (optional)</FormLabel>
												<Select onValueChange={(v) => field.onChange(v === "__none__" ? undefined : v)} value={field.value ?? "__none__"}>
													<FormControl>
														<SelectTrigger>
															<SelectValue placeholder="Any chain" />
														</SelectTrigger>
													</FormControl>
													<SelectContent>
														<SelectItem value="__none__">Any chain</SelectItem>
														{CHAINS.map((c) => (
															<SelectItem key={c} value={c}>
																{c}
															</SelectItem>
														))}
													</SelectContent>
												</Select>
												<FormMessage />
											</FormItem>
										)}
									/>
									<FormField
										control={form.control}
										name="token"
										render={({ field }) => (
											<FormItem>
												<FormLabel>Token (optional)</FormLabel>
												<Select onValueChange={(v) => field.onChange(v === "__none__" ? undefined : v)} value={field.value ?? "__none__"}>
													<FormControl>
														<SelectTrigger>
															<SelectValue placeholder="Any token" />
														</SelectTrigger>
													</FormControl>
													<SelectContent>
														<SelectItem value="__none__">Any token</SelectItem>
														{TOKENS.map((t) => (
															<SelectItem key={t} value={t}>
																{t}
															</SelectItem>
														))}
													</SelectContent>
												</Select>
												<FormMessage />
											</FormItem>
										)}
									/>
								</div>
							</>
						)}

						{(targetKind === "MARKET_PD" || targetKind === "MARKET_ANCHOR" || targetKind === "MARKET_CONTROL") && (
							<>
								<FormField
									control={form.control}
									name="marketSelection"
									render={({ field }) => (
										<FormItem>
											<FormLabel>Market</FormLabel>
											<Select onValueChange={field.onChange} value={field.value}>
												<FormControl>
													<SelectTrigger>
														<SelectValue placeholder={markets.length === 0 ? "Loading…" : "Select a market"} />
													</SelectTrigger>
												</FormControl>
												<SelectContent className="max-h-64">
													{markets.map((m) => (
														<SelectItem key={`${m.marketConfigId}-${m.chainId}-${m.marketIdHex}`} value={serializeMarket(m)}>
															{m.protocol} · {m.label} · chain {m.chainId}
														</SelectItem>
													))}
												</SelectContent>
											</Select>
											<FormMessage />
										</FormItem>
									)}
								/>

								{(targetKind === "MARKET_ANCHOR" || targetKind === "MARKET_CONTROL") && (
									<FormField
										control={form.control}
										name="subCategory"
										render={({ field }) => (
											<FormItem>
												<FormLabel>{targetKind === "MARKET_ANCHOR" ? "Anchor" : "Control modifier"}</FormLabel>
												<Select onValueChange={field.onChange} value={field.value}>
													<FormControl>
														<SelectTrigger>
															<SelectValue
																placeholder={selectedMarket ? (subCategoryOptions.length === 0 ? "No recent observations" : "Select sub-category") : "Pick a market first"}
															/>
														</SelectTrigger>
													</FormControl>
													<SelectContent className="max-h-64">
														{subCategoryOptions.map((sub) => (
															<SelectItem key={sub} value={sub}>
																{sub}
															</SelectItem>
														))}
													</SelectContent>
												</Select>
												<p className="text-muted-foreground text-xs">Pulled from the most recent score snapshots for the chosen market.</p>
												<FormMessage />
											</FormItem>
										)}
									/>
								)}
							</>
						)}

						<div className="grid grid-cols-2 gap-3">
							<FormField
								control={form.control}
								name="severity"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Severity</FormLabel>
										<Select onValueChange={field.onChange} value={field.value}>
											<FormControl>
												<SelectTrigger>
													<SelectValue />
												</SelectTrigger>
											</FormControl>
											<SelectContent>
												<SelectItem value="INFO">Info</SelectItem>
												<SelectItem value="WARNING">Warning</SelectItem>
												<SelectItem value="CRITICAL">Critical</SelectItem>
											</SelectContent>
										</Select>
										<FormMessage />
									</FormItem>
								)}
							/>

							<FormField
								control={form.control}
								name="ruleKind"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Rule Type</FormLabel>
										<Select onValueChange={field.onChange} value={field.value}>
											<FormControl>
												<SelectTrigger>
													<SelectValue />
												</SelectTrigger>
											</FormControl>
											<SelectContent>
												<SelectItem value="THRESHOLD">Threshold</SelectItem>
												<SelectItem value="RATE_OF_CHANGE">Rate of Change</SelectItem>
												<SelectItem value="STDDEV_DEVIATION">Std-dev Deviation</SelectItem>
											</SelectContent>
										</Select>
										<FormMessage />
									</FormItem>
								)}
							/>
						</div>

						{ruleKind === "THRESHOLD" && (
							<div className="rounded-md border p-3 space-y-3">
								<p className="text-muted-foreground text-xs">Fires when the metric value crosses a threshold</p>
								<div className="grid grid-cols-2 gap-3">
									<FormField
										control={form.control}
										name="thresholdOperator"
										render={({ field }) => (
											<FormItem>
												<FormLabel>Operator</FormLabel>
												<Select onValueChange={field.onChange} value={field.value}>
													<FormControl>
														<SelectTrigger>
															<SelectValue placeholder="Select" />
														</SelectTrigger>
													</FormControl>
													<SelectContent>
														{THRESHOLD_OPERATORS.map((op) => (
															<SelectItem key={op} value={op}>
																{op}
															</SelectItem>
														))}
													</SelectContent>
												</Select>
												<FormMessage />
											</FormItem>
										)}
									/>
									<FormField
										control={form.control}
										name="thresholdValue"
										render={({ field }) => (
											<FormItem>
												<FormLabel>Value</FormLabel>
												<FormControl>
													<Input type="number" step="any" placeholder="0" {...field} value={field.value ?? ""} />
												</FormControl>
												<FormMessage />
											</FormItem>
										)}
									/>
								</div>
								<FormField
									control={form.control}
									name="thresholdWindowSeconds"
									render={({ field }) => (
										<FormItem>
											<FormLabel>Window (seconds, 0 = latest only)</FormLabel>
											<FormControl>
												<Input type="number" min={0} step={1} placeholder="0" {...field} value={field.value ?? ""} />
											</FormControl>
											<FormMessage />
										</FormItem>
									)}
								/>
							</div>
						)}

						{ruleKind === "RATE_OF_CHANGE" && (
							<div className="rounded-md border p-3 space-y-3">
								<p className="text-muted-foreground text-xs">Fires when the percent change over the window exceeds the magnitude (sign controlled by direction)</p>
								<div className="grid grid-cols-2 gap-3">
									<FormField
										control={form.control}
										name="rateOfChangeDeltaPct"
										render={({ field }) => (
											<FormItem>
												<FormLabel>Delta % magnitude</FormLabel>
												<FormControl>
													<Input type="number" min={0} step="any" placeholder="10" {...field} value={field.value ?? ""} />
												</FormControl>
												<FormMessage />
											</FormItem>
										)}
									/>
									<FormField
										control={form.control}
										name="rateOfChangeWindowSeconds"
										render={({ field }) => (
											<FormItem>
												<FormLabel>Window (seconds)</FormLabel>
												<FormControl>
													<Input type="number" min={1} step={1} placeholder="3600" {...field} value={field.value ?? ""} />
												</FormControl>
												<FormMessage />
											</FormItem>
										)}
									/>
								</div>
								<FormField
									control={form.control}
									name="rateOfChangeDirection"
									render={({ field }) => (
										<FormItem>
											<FormLabel>Direction</FormLabel>
											<Select onValueChange={field.onChange} value={field.value ?? "both"}>
												<FormControl>
													<SelectTrigger>
														<SelectValue />
													</SelectTrigger>
												</FormControl>
												<SelectContent>
													<SelectItem value="above">Rise only (above)</SelectItem>
													<SelectItem value="below">Drop only (below)</SelectItem>
													<SelectItem value="both">Either direction (both)</SelectItem>
												</SelectContent>
											</Select>
											<FormMessage />
										</FormItem>
									)}
								/>
							</div>
						)}

						{ruleKind === "STDDEV_DEVIATION" && (
							<div className="rounded-md border p-3 space-y-3">
								<p className="text-muted-foreground text-xs">Fires when the latest value is more than (multiplier × stddev) from the historical mean</p>
								<div className="grid grid-cols-2 gap-3">
									<FormField
										control={form.control}
										name="stddevMultiplier"
										render={({ field }) => (
											<FormItem>
												<FormLabel>Multiplier (× stddev)</FormLabel>
												<FormControl>
													<Input type="number" min={0} step="any" placeholder="1" {...field} value={field.value ?? ""} />
												</FormControl>
												<FormMessage />
											</FormItem>
										)}
									/>
									<FormField
										control={form.control}
										name="stddevLookbackSeconds"
										render={({ field }) => (
											<FormItem>
												<FormLabel>Lookback (seconds)</FormLabel>
												<FormControl>
													<Input type="number" min={1} step={1} placeholder="86400" {...field} value={field.value ?? ""} />
												</FormControl>
												<FormMessage />
											</FormItem>
										)}
									/>
								</div>
								<FormField
									control={form.control}
									name="stddevDirection"
									render={({ field }) => (
										<FormItem>
											<FormLabel>Direction</FormLabel>
											<Select onValueChange={field.onChange} value={field.value ?? "both"}>
												<FormControl>
													<SelectTrigger>
														<SelectValue />
													</SelectTrigger>
												</FormControl>
												<SelectContent>
													<SelectItem value="above">Spike only (above)</SelectItem>
													<SelectItem value="below">Dip only (below)</SelectItem>
													<SelectItem value="both">Either direction (both)</SelectItem>
												</SelectContent>
											</Select>
											<FormMessage />
										</FormItem>
									)}
								/>
							</div>
						)}

						{isOperatorEditor && (
							<FormField
								control={form.control}
								name="isTemplate"
								render={({ field }) => (
									<FormItem className="flex items-center justify-between rounded-md border px-3 py-2">
										<div>
											<FormLabel className="text-sm">Operator template</FormLabel>
											<p className="text-muted-foreground text-xs">Visible to all teams; not bound to your team</p>
										</div>
										<FormControl>
											<Switch checked={field.value} onCheckedChange={field.onChange} />
										</FormControl>
									</FormItem>
								)}
							/>
						)}

						<FormField
							control={form.control}
							name="isEnabled"
							render={({ field }) => (
								<FormItem className="flex items-center justify-between rounded-md border px-3 py-2">
									<FormLabel className="text-sm">Enabled</FormLabel>
									<FormControl>
										<Switch checked={field.value} onCheckedChange={field.onChange} />
									</FormControl>
								</FormItem>
							)}
						/>

						<DialogFooter>
							<Button type="button" variant="outline" onClick={() => setOpen(false)} disabled={submitting}>
								Cancel
							</Button>
							<Button type="submit" disabled={submitting}>
								{submitting ? "Creating…" : "Create Alert"}
							</Button>
						</DialogFooter>
					</form>
				</Form>
			</DialogContent>
		</Dialog>
	)
}
