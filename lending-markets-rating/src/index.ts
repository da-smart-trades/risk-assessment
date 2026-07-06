#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import 'dotenv/config'
import { Command } from 'commander'
import { registerCommands } from './commands'

main().catch((error: unknown) => {
  console.error(`Error: ${errorMessage(error)}`)
  process.exitCode = 1
})

async function main(): Promise<void> {
  const program = new Command()

  program
    .name('lending-markets-rating')
    .description('Lending Markets Rating')
    .showHelpAfterError()

  registerCommands(program)

  if (process.argv.slice(2).length === 0) {
    program.outputHelp()
    return
  }

  await program.parseAsync(process.argv)
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
