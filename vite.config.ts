// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import litestar from "litestar-vite-plugin"
import { defineConfig } from "vite"

export default defineConfig({
	clearScreen: false,
	publicDir: "public",
	server: {
		cors: true,
		watch: {
			ignored: ["**/.venv/**", "**/node_modules/**"],
		},
	},
	plugins: [
		tailwindcss(),
		react(),
		litestar({
			input: ["resources/main.tsx", "resources/main.css"],
		}),
	],
	resolve: {
		alias: {
			"@": "/resources",
		},
	},
})
