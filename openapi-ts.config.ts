// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "./resources/lib/generated/openapi.json",
  output: "./resources/lib/generated/api",
  plugins: ["@hey-api/typescript", "@hey-api/schemas", "@hey-api/sdk"],
});
