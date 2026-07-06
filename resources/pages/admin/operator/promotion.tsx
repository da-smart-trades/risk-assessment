// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, router } from "@inertiajs/react"
import { ShieldCheck } from "lucide-react"
import type React from "react"
import { useState } from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { toast } from "@/components/ui/use-toast"
import { AdminLayout } from "@/layouts/admin-layout"
import { route } from "@/lib/generated/routes"

interface OperatorMember {
	memberId: string
	email: string
	name: string | null
	role: string
	isOwner: boolean
	canPromote: boolean
}

interface Props {
	teamName: string | null
	members: OperatorMember[]
}

const ROLE_LABELS: Record<string, string> = {
	owner: "Owner",
	operator_tenant_admin: "Tenant admin",
	operator_support: "Support",
	admin: "Admin",
	editor: "Editor",
	member: "Member",
}

export default function OperatorPromotion({ teamName, members }: Props) {
	const [promotingId, setPromotingId] = useState<string | null>(null)

	const promote = (member: OperatorMember) => {
		setPromotingId(member.memberId)
		router.post(
			route("admin.operator.promote", { member_id: member.memberId }),
			{},
			{
				preserveScroll: true,
				onSuccess: () => toast({ description: `${member.email} promoted to tenant-admin.`, variant: "success" }),
				onError: (errors) => toast({ description: Object.values(errors)[0] ?? "Promotion failed.", variant: "destructive" }),
				onFinish: () => setPromotingId(null),
			},
		)
	}

	return (
		<>
			<Head title="Operator promotion" />
			<Header title="Operator team — role management" />
			<Container>
				<Card className="mx-auto max-w-3xl">
					<CardHeader>
						<CardTitle className="flex items-center gap-2">
							<ShieldCheck className="h-5 w-5" />
							{teamName ?? "Operator team"} members
						</CardTitle>
						<CardDescription>
							Promote a support member to <strong>tenant-admin</strong> to grant cross-customer write access. Promotion is audited; the member must sign in again for the new role
							to take effect.
						</CardDescription>
					</CardHeader>
					<CardContent>
						{members.length === 0 ? (
							<p className="text-muted-foreground text-sm">No operator-team members found.</p>
						) : (
							<div className="space-y-2">
								{members.map((member) => (
									<div key={member.memberId} className="flex items-center justify-between rounded-lg border p-3">
										<div className="flex items-center gap-3">
											<div>
												<p className="font-medium text-sm">{member.name || member.email}</p>
												<p className="text-muted-foreground text-xs">{member.email}</p>
											</div>
											<Badge variant={member.role === "operator_support" ? "secondary" : "default"}>{ROLE_LABELS[member.role] ?? member.role}</Badge>
										</div>
										{member.canPromote ? (
											<Button size="sm" onClick={() => promote(member)} disabled={promotingId === member.memberId}>
												Promote to tenant-admin
											</Button>
										) : (
											<span className="text-muted-foreground text-xs">{member.isOwner ? "Owner" : "—"}</span>
										)}
									</div>
								))}
							</div>
						)}
					</CardContent>
				</Card>
			</Container>
		</>
	)
}

OperatorPromotion.layout = (page: React.ReactNode) => <AdminLayout>{page}</AdminLayout>
