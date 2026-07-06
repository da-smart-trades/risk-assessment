// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import type { Command } from 'commander'
import { registerAaveCommand } from './aave'
import { registerMapleCommand } from './maple'
import { registerMorphoCommand } from './morpho'
import { registerSparkCommand } from './spark'

export function registerCommands(program: Command): void {
  registerMorphoCommand(program)
  registerAaveCommand(program)
  registerSparkCommand(program)
  registerMapleCommand(program)
}
