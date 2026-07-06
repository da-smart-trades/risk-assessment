// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, router } from "@inertiajs/react"
import axios from "axios"
import { KeyRound, ShieldCheck } from "lucide-react"
import { useCallback, useState } from "react"
import { Container } from "@/components/container"
import { Header } from "@/components/header"
import { InputError } from "@/components/input-error"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toast } from "@/components/ui/use-toast"
import { AppLayout } from "@/layouts/app-layout"

interface Props {
	hasTotp: boolean
	hasPasskey: boolean
	factorCount: number
	enrollComplete: boolean
}

interface MfaSetup {
	secret: string
	qrCode: string
}

interface MfaBackupCodes {
	codes: string[]
}

function base64urlToBytes(b64url: string): Uint8Array {
	const padded = b64url.replace(/-/g, "+").replace(/_/g, "/")
	const padding = "=".repeat((4 - (padded.length % 4)) % 4)
	const raw = atob(padded + padding)
	const bytes = new Uint8Array(raw.length)
	for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)
	return bytes
}

function bytesToBase64url(bytes: ArrayBuffer): string {
	const view = new Uint8Array(bytes)
	let bin = ""
	for (let i = 0; i < view.length; i++) bin += String.fromCharCode(view[i])
	return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}

export default function MfaEnroll({ hasTotp, hasPasskey, factorCount, enrollComplete }: Props) {
	const [setup, setSetup] = useState<MfaSetup | null>(null)
	const [code, setCode] = useState("")
	const [codeError, setCodeError] = useState<string | null>(null)
	const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null)
	const [passkeyName, setPasskeyName] = useState("")
	const [passkeyError, setPasskeyError] = useState<string | null>(null)
	const [busy, setBusy] = useState(false)

	const startTotp = useCallback(async () => {
		setBusy(true)
		try {
			const { data } = await axios.post<MfaSetup>("/settings/security/mfa/totp/begin")
			setSetup(data)
		} catch {
			toast({ description: "Could not start TOTP setup.", variant: "destructive" })
		} finally {
			setBusy(false)
		}
	}, [])

	const confirmTotp = useCallback(
		async (e: React.FormEvent) => {
			e.preventDefault()
			setCodeError(null)
			setBusy(true)
			try {
				const { data } = await axios.post<MfaBackupCodes>("/settings/security/mfa/totp/confirm", { code })
				setRecoveryCodes(data.codes)
				setSetup(null)
				toast({ description: "Authenticator app enrolled.", variant: "success" })
			} catch (err) {
				setCodeError("Invalid code. Try again.")
				if (err instanceof Error) {
					/* keep generic */
				}
			} finally {
				setBusy(false)
			}
		},
		[code],
	)

	const enrollPasskey = useCallback(async () => {
		setPasskeyError(null)
		setBusy(true)
		try {
			const { data } = await axios.post<{ optionsJson: string }>("/settings/security/mfa/passkey/begin", { deviceName: passkeyName || "Passkey" })
			const options = JSON.parse(data.optionsJson)
			const publicKey = {
				...options,
				challenge: base64urlToBytes(options.challenge),
				user: { ...options.user, id: base64urlToBytes(options.user.id) },
				excludeCredentials: (options.excludeCredentials || []).map((c: { id: string; type: string }) => ({ ...c, id: base64urlToBytes(c.id) })),
			}
			const credential = (await navigator.credentials.create({ publicKey: publicKey as PublicKeyCredentialCreationOptions })) as PublicKeyCredential | null
			if (!credential) {
				setPasskeyError("Browser did not return a credential.")
				return
			}
			const response = credential.response as AuthenticatorAttestationResponse
			const responseJson = JSON.stringify({
				id: credential.id,
				rawId: bytesToBase64url(credential.rawId),
				type: credential.type,
				response: {
					attestationObject: bytesToBase64url(response.attestationObject),
					clientDataJSON: bytesToBase64url(response.clientDataJSON),
				},
				clientExtensionResults: {},
			})
			const { data: codes } = await axios.post<MfaBackupCodes>("/settings/security/mfa/passkey/finish", { deviceName: passkeyName || "Passkey", responseJson })
			if (codes.codes.length > 0) setRecoveryCodes(codes.codes)
			toast({ description: "Passkey enrolled.", variant: "success" })
			router.reload()
		} catch (err) {
			setPasskeyError(err instanceof Error ? err.message : "Could not enroll passkey.")
		} finally {
			setBusy(false)
		}
	}, [passkeyName])

	const downloadRecoveryCodes = () => {
		if (!recoveryCodes) return
		const blob = new Blob([recoveryCodes.join("\n")], { type: "text/plain" })
		const a = document.createElement("a")
		a.href = URL.createObjectURL(blob)
		a.download = "certora-recovery-codes.txt"
		a.click()
	}

	const finish = () => {
		router.visit("/dashboard")
	}

	return (
		<>
			<Head title="Secure your account" />
			<Header title="Secure your account" />
			<Container>
				<div className="mx-auto grid w-full max-w-3xl gap-6">
					<Card>
						<CardHeader>
							<CardTitle className="flex items-center gap-2">
								<ShieldCheck className="h-5 w-5" />
								Multi-factor authentication
							</CardTitle>
							<CardDescription>
								Enroll a second factor before accessing your account. {factorCount > 0 && `You have ${factorCount} factor${factorCount > 1 ? "s" : ""} enrolled.`}
							</CardDescription>
						</CardHeader>
					</Card>

					{recoveryCodes && (
						<Card>
							<CardHeader>
								<CardTitle>Recovery codes</CardTitle>
								<CardDescription>Save these — each works once and you won't see them again.</CardDescription>
							</CardHeader>
							<CardContent className="space-y-4">
								<pre className="rounded-md border bg-muted p-4 font-mono text-sm">{recoveryCodes.join("\n")}</pre>
								<div className="flex gap-2">
									<Button type="button" onClick={downloadRecoveryCodes} variant="outline">
										Download
									</Button>
									<Button type="button" onClick={finish}>
										Continue to dashboard
									</Button>
								</div>
							</CardContent>
						</Card>
					)}

					{!recoveryCodes && (
						<>
							<Card>
								<CardHeader>
									<CardTitle>Authenticator app</CardTitle>
									<CardDescription>{hasTotp ? "Enrolled." : "Scan the QR code in Google Authenticator, 1Password, or similar."}</CardDescription>
								</CardHeader>
								<CardContent className="space-y-4">
									{!hasTotp && !setup && (
										<Button type="button" onClick={startTotp} disabled={busy}>
											Set up authenticator
										</Button>
									)}
									{setup && (
										<form onSubmit={confirmTotp} className="space-y-4">
											<img alt="TOTP QR" src={setup.qrCode} className="mx-auto h-48 w-48" />
											<p className="text-center font-mono text-xs">{setup.secret}</p>
											<div>
												<Label htmlFor="totp-code">6-digit code</Label>
												<Input
													id="totp-code"
													inputMode="numeric"
													pattern="[0-9]*"
													maxLength={6}
													value={code}
													onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
													className="text-center font-mono"
													placeholder="000000"
													autoFocus
												/>
												<InputError message={codeError ?? undefined} className="mt-2" />
											</div>
											<Button type="submit" disabled={busy}>
												Confirm
											</Button>
										</form>
									)}
								</CardContent>
							</Card>

							<Card>
								<CardHeader>
									<CardTitle className="flex items-center gap-2">
										<KeyRound className="h-5 w-5" />
										Passkey
									</CardTitle>
									<CardDescription>{hasPasskey ? "At least one passkey enrolled." : "Use Touch ID, Face ID, Windows Hello, or a security key."}</CardDescription>
								</CardHeader>
								<CardContent className="space-y-4">
									<div>
										<Label htmlFor="passkey-name">Device label</Label>
										<Input id="passkey-name" value={passkeyName} onChange={(e) => setPasskeyName(e.target.value)} placeholder="MacBook Touch ID" />
									</div>
									<Button type="button" onClick={enrollPasskey} disabled={busy}>
										Add passkey
									</Button>
									{passkeyError && <p className="text-destructive text-sm">{passkeyError}</p>}
								</CardContent>
							</Card>

							{enrollComplete && (
								<Card>
									<CardContent className="pt-6">
										<Button type="button" onClick={finish} className="w-full">
											Continue to dashboard
										</Button>
									</CardContent>
								</Card>
							)}
						</>
					)}
				</div>
			</Container>
		</>
	)
}

MfaEnroll.layout = (page: React.ReactNode) => <AppLayout>{page}</AppLayout>
