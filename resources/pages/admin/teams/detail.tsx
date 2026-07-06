// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, Link, router, useForm } from "@inertiajs/react"
import { format } from "date-fns"
import { ArrowLeft, Crown, KeyRound, LifeBuoy, MoreHorizontal, Trash2, Unlock, UserMinus, UserPlus } from "lucide-react"
import { useState } from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
	AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { useToast } from "@/components/ui/use-toast"
import { AdminLayout } from "@/layouts/admin-layout"
import { route } from "@/lib/generated/routes"
import { getGravatarUrl, getInitials } from "@/lib/utils"

interface TeamMemberInfo {
	id: string
	userId: string
	email: string
	name: string | null
	role: string
	isOwner: boolean
	avatarUrl: string | null
}

interface AdminTeamDetail {
	id: string
	name: string
	slug: string
	description: string | null
	isActive: boolean
	isOperator: boolean
	members: TeamMemberInfo[]
	createdAt: string | null
	updatedAt: string | null
}

interface Props {
	team: AdminTeamDetail
}

const roleStyles: Record<string, string> = {
	owner: "bg-[#EDB641] text-[#202235]",
	admin: "bg-[#202235] text-white",
	editor: "bg-[#FFD480] text-[#202235]",
	member: "bg-[#DCDFE4] text-[#202235]",
}

export default function AdminTeamDetail({ team }: Props) {
	const { toast } = useToast()

	const { data, setData, patch, processing } = useForm({
		name: team.name,
		description: team.description || "",
		isActive: team.isActive,
	})

	const inviteForm = useForm({
		email: "",
		role: "member",
		force_provider: "",
		out_of_domain_override: false,
	})

	const [outOfDomainPrompt, setOutOfDomainPrompt] = useState<string | null>(null)

	const handleSubmit = (e: React.FormEvent) => {
		e.preventDefault()
		patch(`/admin/teams/${team.id}/`, {
			preserveScroll: true,
			onSuccess: () => {
				toast({ description: "Team updated successfully.", variant: "success" })
			},
		})
	}

	const submitInvite = (override: boolean) => {
		inviteForm.transform((data) => ({
			...data,
			force_provider: data.force_provider || null,
			out_of_domain_override: override,
		}))
		inviteForm.post(`/admin/teams/${team.id}/members/invite/`, {
			preserveScroll: true,
			onSuccess: () => {
				toast({ description: `Invitation sent to ${inviteForm.data.email}.`, variant: "success" })
				inviteForm.reset()
				setOutOfDomainPrompt(null)
			},
			onError: (errors) => {
				const oodMsg = (errors as Record<string, string>).out_of_domain_required
				if (oodMsg) {
					setOutOfDomainPrompt(oodMsg)
				}
			},
		})
	}

	const handleInviteSubmit = (e: React.FormEvent) => {
		e.preventDefault()
		submitInvite(false)
	}

	const handleRemoveMember = (memberId: string) => {
		router.delete(`/admin/teams/${team.id}/members/${memberId}/`, {
			preserveScroll: true,
			onSuccess: () => {
				toast({ description: "Member removed successfully.", variant: "success" })
			},
		})
	}

	const handleForceUnlock = (memberId: string) => {
		router.post(
			`/admin/teams/${team.id}/members/${memberId}/force-unlock/`,
			{},
			{
				preserveScroll: true,
				onSuccess: () => {
					toast({ description: "Account unlocked.", variant: "success" })
				},
			},
		)
	}

	const handleResetMfa = (memberId: string) => {
		router.post(
			`/admin/teams/${team.id}/members/${memberId}/reset-mfa/`,
			{},
			{
				preserveScroll: true,
				onSuccess: () => {
					toast({ description: "MFA factors reset. User keeps their password.", variant: "success" })
				},
			},
		)
	}

	const handleTotalRecovery = (memberId: string) => {
		router.post(
			`/admin/teams/${team.id}/members/${memberId}/total-recovery/`,
			{},
			{
				preserveScroll: true,
				onSuccess: () => {
					toast({ description: "Total recovery email sent.", variant: "success" })
				},
			},
		)
	}

	const handleMakeOwner = (memberId: string) => {
		router.post(
			`/admin/teams/${team.id}/members/${memberId}/make-owner/`,
			{},
			{
				preserveScroll: true,
				onSuccess: () => {
					toast({ description: "Ownership transferred.", variant: "success" })
				},
			},
		)
	}

	const handlePromoteOperator = (memberId: string) => {
		router.post(
			route("admin.operator.promote", { member_id: memberId }),
			{},
			{
				preserveScroll: true,
				onSuccess: () => {
					toast({ description: "Promoted to operator tenant-admin.", variant: "success" })
				},
				onError: (errors) => {
					toast({ description: Object.values(errors)[0] ?? "Promotion failed.", variant: "destructive" })
				},
			},
		)
	}

	return (
		<>
			<Head title={`${team.name} - Admin`} />
			<Header title={team.name}>
				<Link href="/admin/teams">
					<Button variant="outline">
						<ArrowLeft className="mr-2 h-4 w-4" />
						Back to Teams
					</Button>
				</Link>
			</Header>
			<Container>
				<div className="grid gap-6 lg:grid-cols-3">
					{/* Team Info */}
					<div className="lg:col-span-2">
						<Card>
							<CardHeader>
								<CardTitle>Team Details</CardTitle>
								<CardDescription>Update team information</CardDescription>
							</CardHeader>
							<CardContent>
								<form onSubmit={handleSubmit} className="space-y-4">
									<div className="space-y-2">
										<Label htmlFor="name">Name</Label>
										<Input id="name" value={data.name} onChange={(e) => setData("name", e.target.value)} />
									</div>

									<div className="space-y-2">
										<Label htmlFor="description">Description</Label>
										<Textarea id="description" value={data.description} onChange={(e) => setData("description", e.target.value)} rows={3} />
									</div>

									<div className="flex items-center space-x-2">
										<Switch id="isActive" checked={data.isActive} onCheckedChange={(checked) => setData("isActive", checked)} />
										<Label htmlFor="isActive">Active</Label>
									</div>

									<Button type="submit" disabled={processing}>
										{processing ? "Saving..." : "Save Changes"}
									</Button>
								</form>
							</CardContent>
						</Card>

						{/* Invite Member */}
						<Card className="mt-6">
							<CardHeader>
								<CardTitle>Invite Member</CardTitle>
								<CardDescription>Sends an invitation email. The invitee completes sign-in via OIDC (Google, Microsoft, or GitHub).</CardDescription>
							</CardHeader>
							<CardContent>
								<form onSubmit={handleInviteSubmit} className="space-y-4">
									<div className="grid gap-4 md:grid-cols-2">
										<div className="space-y-2">
											<Label htmlFor="invite-email">Email</Label>
											<Input
												id="invite-email"
												type="email"
												value={inviteForm.data.email}
												onChange={(e) => inviteForm.setData("email", e.target.value)}
												placeholder="name@company.com"
												required
											/>
											{inviteForm.errors.email && <p className="text-destructive text-sm">{inviteForm.errors.email}</p>}
										</div>
										<div className="space-y-2">
											<Label htmlFor="invite-role">Role</Label>
											<Select value={inviteForm.data.role} onValueChange={(v) => inviteForm.setData("role", v)}>
												<SelectTrigger id="invite-role">
													<SelectValue />
												</SelectTrigger>
												<SelectContent>
													<SelectItem value="member">Member</SelectItem>
													<SelectItem value="editor">Editor</SelectItem>
													<SelectItem value="admin">Admin</SelectItem>
												</SelectContent>
											</Select>
										</div>
									</div>
									<div className="space-y-2">
										<Label htmlFor="invite-provider">Force provider (optional)</Label>
										<Select value={inviteForm.data.force_provider || "any"} onValueChange={(v) => inviteForm.setData("force_provider", v === "any" ? "" : v)}>
											<SelectTrigger id="invite-provider">
												<SelectValue />
											</SelectTrigger>
											<SelectContent>
												<SelectItem value="any">Let invitee choose</SelectItem>
												<SelectItem value="google">Google</SelectItem>
												<SelectItem value="microsoft">Microsoft</SelectItem>
												<SelectItem value="github">GitHub</SelectItem>
											</SelectContent>
										</Select>
										<p className="text-muted-foreground text-xs">When set, the invitation link routes directly into that provider's sign-in. Otherwise the invitee picks.</p>
									</div>
									<Button type="submit" disabled={inviteForm.processing}>
										<UserPlus className="mr-2 h-4 w-4" />
										{inviteForm.processing ? "Sending..." : "Send Invitation"}
									</Button>
								</form>
							</CardContent>
						</Card>

						<AlertDialog open={outOfDomainPrompt !== null} onOpenChange={(open) => !open && setOutOfDomainPrompt(null)}>
							<AlertDialogContent>
								<AlertDialogHeader>
									<AlertDialogTitle>Confirm out-of-domain invitation</AlertDialogTitle>
									<AlertDialogDescription>{outOfDomainPrompt}</AlertDialogDescription>
								</AlertDialogHeader>
								<AlertDialogFooter>
									<AlertDialogCancel>Cancel</AlertDialogCancel>
									<AlertDialogAction onClick={() => submitInvite(true)}>Send anyway</AlertDialogAction>
								</AlertDialogFooter>
							</AlertDialogContent>
						</AlertDialog>

						{/* Members */}
						<Card className="mt-6">
							<CardHeader>
								<CardTitle>Members ({team.members.length})</CardTitle>
								<CardDescription>Team member management</CardDescription>
							</CardHeader>
							<CardContent>
								{team.members.length > 0 ? (
									<div className="space-y-3">
										{team.members.map((member) => (
											<div key={member.id} className="flex items-center justify-between rounded-md border p-3">
												<div className="flex items-center gap-3">
													<Avatar className="h-10 w-10">
														<AvatarImage src={member.avatarUrl ?? getGravatarUrl(member.email)} />
														<AvatarFallback>{getInitials(member.email)}</AvatarFallback>
													</Avatar>
													<div>
														<p className="font-medium">{member.name || member.email}</p>
														{member.name && <p className="text-muted-foreground text-sm">{member.email}</p>}
													</div>
												</div>
												<div className="flex items-center gap-2">
													<Badge className={roleStyles[member.isOwner ? "owner" : member.role] || roleStyles.member}>{member.isOwner ? "Owner" : member.role}</Badge>
													{team.isOperator && member.role === "operator_support" && (
														<Button size="sm" variant="outline" onClick={() => handlePromoteOperator(member.id)}>
															Promote
														</Button>
													)}
													{!member.isOwner && (
														<DropdownMenu>
															<DropdownMenuTrigger asChild>
																<Button variant="ghost" size="icon">
																	<MoreHorizontal className="h-4 w-4" />
																</Button>
															</DropdownMenuTrigger>
															<DropdownMenuContent align="end">
																<DropdownMenuLabel>Account actions</DropdownMenuLabel>
																<DropdownMenuItem onSelect={() => handleForceUnlock(member.id)}>
																	<Unlock className="mr-2 h-4 w-4" /> Force unlock
																</DropdownMenuItem>
																<DropdownMenuItem onSelect={() => handleResetMfa(member.id)}>
																	<KeyRound className="mr-2 h-4 w-4" /> Reset MFA only
																</DropdownMenuItem>
																<DropdownMenuItem onSelect={() => handleTotalRecovery(member.id)}>
																	<LifeBuoy className="mr-2 h-4 w-4" /> Total recovery
																</DropdownMenuItem>
																<DropdownMenuSeparator />
																<DropdownMenuLabel>Team role</DropdownMenuLabel>
																<AlertDialog>
																	<AlertDialogTrigger asChild>
																		<DropdownMenuItem onSelect={(e) => e.preventDefault()}>
																			<Crown className="mr-2 h-4 w-4" /> Make owner
																		</DropdownMenuItem>
																	</AlertDialogTrigger>
																	<AlertDialogContent>
																		<AlertDialogHeader>
																			<AlertDialogTitle>Transfer ownership</AlertDialogTitle>
																			<AlertDialogDescription>
																				Make {member.email} the sole owner of {team.name}? The current owner is demoted to admin. The new owner can manage the team and enforce
																				single sign-on.
																			</AlertDialogDescription>
																		</AlertDialogHeader>
																		<AlertDialogFooter>
																			<AlertDialogCancel>Cancel</AlertDialogCancel>
																			<AlertDialogAction onClick={() => handleMakeOwner(member.id)}>Make owner</AlertDialogAction>
																		</AlertDialogFooter>
																	</AlertDialogContent>
																</AlertDialog>
																<DropdownMenuSeparator />
																<AlertDialog>
																	<AlertDialogTrigger asChild>
																		<DropdownMenuItem onSelect={(e) => e.preventDefault()} className="text-destructive focus:text-destructive">
																			<UserMinus className="mr-2 h-4 w-4" /> Remove from team
																		</DropdownMenuItem>
																	</AlertDialogTrigger>
																	<AlertDialogContent>
																		<AlertDialogHeader>
																			<AlertDialogTitle>Remove Member</AlertDialogTitle>
																			<AlertDialogDescription>Are you sure you want to remove {member.email} from this team?</AlertDialogDescription>
																		</AlertDialogHeader>
																		<AlertDialogFooter>
																			<AlertDialogCancel>Cancel</AlertDialogCancel>
																			<AlertDialogAction onClick={() => handleRemoveMember(member.id)}>Remove</AlertDialogAction>
																		</AlertDialogFooter>
																	</AlertDialogContent>
																</AlertDialog>
															</DropdownMenuContent>
														</DropdownMenu>
													)}
												</div>
											</div>
										))}
									</div>
								) : (
									<p className="text-muted-foreground">No members</p>
								)}
							</CardContent>
						</Card>
					</div>

					{/* Sidebar */}
					<div className="space-y-6">
						{/* Status */}
						<Card>
							<CardHeader>
								<CardTitle>Information</CardTitle>
							</CardHeader>
							<CardContent className="space-y-4">
								<div>
									<p className="text-muted-foreground text-sm">Slug</p>
									<p className="font-mono text-sm">{team.slug}</p>
								</div>

								<div>
									<p className="text-muted-foreground text-sm">Status</p>
									{team.isActive ? (
										<Badge variant="outline" className="border-green-500 text-green-600">
											Active
										</Badge>
									) : (
										<Badge variant="destructive">Inactive</Badge>
									)}
								</div>

								<Separator />

								<div className="space-y-2 text-sm">
									<p>
										<span className="text-muted-foreground">Created:</span> {team.createdAt ? format(new Date(team.createdAt), "MMM d, yyyy 'at' h:mm a") : "-"}
									</p>
									<p>
										<span className="text-muted-foreground">Updated:</span> {team.updatedAt ? format(new Date(team.updatedAt), "MMM d, yyyy 'at' h:mm a") : "-"}
									</p>
								</div>
							</CardContent>
						</Card>

						{/* Danger Zone */}
						<Card className="border-destructive">
							<CardHeader>
								<CardTitle className="text-destructive">Danger Zone</CardTitle>
							</CardHeader>
							<CardContent>
								<AlertDialog>
									<AlertDialogTrigger asChild>
										<Button variant="destructive" className="w-full">
											<Trash2 className="mr-2 h-4 w-4" />
											Delete Team
										</Button>
									</AlertDialogTrigger>
									<AlertDialogContent>
										<AlertDialogHeader>
											<AlertDialogTitle>Delete Team</AlertDialogTitle>
											<AlertDialogDescription>Are you sure you want to delete "{team.name}"? This will remove all team members and cannot be undone.</AlertDialogDescription>
										</AlertDialogHeader>
										<AlertDialogFooter>
											<AlertDialogCancel>Cancel</AlertDialogCancel>
											<AlertDialogAction className="bg-destructive text-destructive-foreground hover:bg-destructive/90" onClick={() => router.delete(`/admin/teams/${team.id}/`)}>
												Delete
											</AlertDialogAction>
										</AlertDialogFooter>
									</AlertDialogContent>
								</AlertDialog>
							</CardContent>
						</Card>
					</div>
				</div>
			</Container>
		</>
	)
}

AdminTeamDetail.layout = (page: React.ReactNode) => <AdminLayout>{page}</AdminLayout>
