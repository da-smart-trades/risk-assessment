// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Head, router, useForm } from "@inertiajs/react"
import axios from "axios"
import { useCallback, useState } from "react"
import { AuthHeroPanel } from "@/components/auth-hero-panel"
import { Icons } from "@/components/icons"
import { InputError } from "@/components/input-error"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { GuestLayout } from "@/layouts/guest-layout"

interface Props {
	hasTotp: boolean
	hasPasskey: boolean
	hasRecovery: boolean
}

type Mode = "totp" | "recovery" | "passkey"

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

export default function MfaVerify({ hasTotp, hasPasskey, hasRecovery }: Props) {
	const initialMode: Mode = hasPasskey ? "passkey" : hasTotp ? "totp" : "recovery"
	const [mode, setMode] = useState<Mode>(initialMode)
	const [passkeyError, setPasskeyError] = useState<string | null>(null)
	const [passkeyBusy, setPasskeyBusy] = useState(false)

	const totpForm = useForm({ code: "" })
	const recoveryForm = useForm({ code: "" })

	const submitTotp = (e: React.FormEvent) => {
		e.preventDefault()
		totpForm.post("/auth/mfa/totp")
	}

	const submitRecovery = (e: React.FormEvent) => {
		e.preventDefault()
		recoveryForm.post("/auth/mfa/recovery")
	}

	const submitPasskey = useCallback(async () => {
		setPasskeyError(null)
		setPasskeyBusy(true)
		try {
			const { data: opts } = await axios.post<{ optionsJson: string }>("/auth/mfa/passkey/options")
			const options = JSON.parse(opts.optionsJson)
			const publicKey = {
				...options,
				challenge: base64urlToBytes(options.challenge),
				allowCredentials: (options.allowCredentials || []).map((c: { id: string; type: string; transports?: string[] }) => ({ ...c, id: base64urlToBytes(c.id) })),
			}
			const credential = (await navigator.credentials.get({ publicKey: publicKey as PublicKeyCredentialRequestOptions })) as PublicKeyCredential | null
			if (!credential) {
				setPasskeyError("No passkey returned by the browser.")
				return
			}
			const response = credential.response as AuthenticatorAssertionResponse
			const responseJson = JSON.stringify({
				id: credential.id,
				rawId: bytesToBase64url(credential.rawId),
				type: credential.type,
				response: {
					authenticatorData: bytesToBase64url(response.authenticatorData),
					clientDataJSON: bytesToBase64url(response.clientDataJSON),
					signature: bytesToBase64url(response.signature),
					userHandle: response.userHandle ? bytesToBase64url(response.userHandle) : null,
				},
				clientExtensionResults: {},
			})
			router.post("/auth/mfa/passkey", { responseJson })
		} catch (err) {
			setPasskeyError(err instanceof Error ? err.message : "Passkey sign-in failed.")
		} finally {
			setPasskeyBusy(false)
		}
	}, [])

	return (
		<>
			<Head title="Multi-Factor Authentication" />
			<AuthHeroPanel description="Secure your account with multi-factor authentication." />

			<div className="flex flex-col justify-center px-4 py-8 sm:px-6 lg:px-8">
				<div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-87.5">
					<div className="flex flex-col space-y-2 text-center">
						<h1 className="flex items-center justify-center gap-2 font-semibold text-2xl tracking-tight">
							<Icons.shield className="h-5 w-5" />
							Verify your sign-in
						</h1>
						<p className="text-muted-foreground text-sm">
							{mode === "totp" && "Enter the 6-digit code from your authenticator app."}
							{mode === "recovery" && "Enter one of your recovery codes."}
							{mode === "passkey" && "Use your passkey to continue."}
						</p>
					</div>

					{mode === "totp" && (
						<form onSubmit={submitTotp} className="space-y-4">
							<div>
								<Label htmlFor="totp-code">Authentication Code</Label>
								<Input
									id="totp-code"
									inputMode="numeric"
									pattern="[0-9]*"
									maxLength={6}
									value={totpForm.data.code}
									onChange={(e) => totpForm.setData("code", e.target.value.replace(/\D/g, ""))}
									className="mt-1 text-center font-mono text-lg tracking-widest"
									placeholder="000000"
									autoComplete="one-time-code"
									autoFocus
								/>
								<InputError message={totpForm.errors.code} className="mt-2" />
							</div>
							<Button type="submit" className="w-full" disabled={totpForm.processing}>
								Verify
							</Button>
						</form>
					)}

					{mode === "recovery" && (
						<form onSubmit={submitRecovery} className="space-y-4">
							<div>
								<Label htmlFor="recovery-code">Recovery Code</Label>
								<Input
									id="recovery-code"
									value={recoveryForm.data.code}
									onChange={(e) => recoveryForm.setData("code", e.target.value.toUpperCase())}
									className="mt-1 font-mono tracking-widest"
									placeholder="XXXX-XXXX"
									autoComplete="off"
									autoFocus
								/>
								<InputError message={recoveryForm.errors.code} className="mt-2" />
							</div>
							<Button type="submit" className="w-full" disabled={recoveryForm.processing}>
								Verify
							</Button>
						</form>
					)}

					{mode === "passkey" && (
						<div className="space-y-4">
							<Button type="button" className="w-full" disabled={passkeyBusy} onClick={submitPasskey}>
								{passkeyBusy ? "Waiting for passkey..." : "Use passkey"}
							</Button>
							{passkeyError && <p className="text-destructive text-sm">{passkeyError}</p>}
						</div>
					)}

					<div className="space-y-2 text-center text-sm">
						{hasTotp && mode !== "totp" && (
							<button type="button" onClick={() => setMode("totp")} className="text-muted-foreground underline-offset-4 hover:text-primary hover:underline">
								Use authenticator code
							</button>
						)}
						{hasPasskey && mode !== "passkey" && (
							<button type="button" onClick={() => setMode("passkey")} className="block w-full text-muted-foreground underline-offset-4 hover:text-primary hover:underline">
								Use passkey
							</button>
						)}
						{hasRecovery && mode !== "recovery" && (
							<button type="button" onClick={() => setMode("recovery")} className="block w-full text-muted-foreground underline-offset-4 hover:text-primary hover:underline">
								Use recovery code
							</button>
						)}
					</div>
				</div>
			</div>
		</>
	)
}

MfaVerify.layout = (page: React.ReactNode) => <GuestLayout>{page}</GuestLayout>
