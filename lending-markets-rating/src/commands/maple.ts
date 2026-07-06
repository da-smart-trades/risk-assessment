// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Command } from 'commander'
import { ReportPrinter } from '../lib/reports/ReportPrinter'
import { DEFAULT_LLM_PROVIDER, normalizeLlmProvider, type LlmProvider } from '../lib/llms/providers'
import { MarketScorePrinter } from '../lib/scoring/MarketScorePrinter'
import { parseChainId, requireChain } from '../lib/web3/chains'
import { MAPLE_CHAINS } from '../protocols/maple/queries/networks'
import { MapleReportBuilder } from '../protocols/maple/report/MapleReportBuilder'
import { normalizeOutput, printGraphOutput, printJsonOutput, scoreReport, type OutputFormat } from '../lib/commands'

export function registerMapleCommand(program: Command): void {
  program
    .command('maple')
    .description('Fetch and print a Maple pool report')
    .option('--output <format>', 'output format: text, json or graph', 'text')
    .option('--score', 'score the report using the selected llm provider')
    .option('--llm <provider>', 'llm provider: claude or openai', DEFAULT_LLM_PROVIDER)
    .argument('<chain-id>')
    .argument('<pool-address>')
    .action(async (
      chainId: string,
      address: string,
      options: { output: string; score?: boolean; llm?: string },
    ) => {
      const output = normalizeOutput(options.output)
      const score = Boolean(options.score)
      const llmProvider = output === 'graph' || score ? normalizeLlmProvider(options.llm) : DEFAULT_LLM_PROVIDER
      await runMapleCommand(
        parseChainId(chainId),
        address,
        output,
        score,
        llmProvider,
      )
    })
}

async function runMapleCommand(
  chainId: number,
  address: string,
  output: OutputFormat,
  score: boolean,
  llmProvider: LlmProvider,
): Promise<void> {
  const chain = requireChain(chainId, MAPLE_CHAINS, 'Maple chain')
  const report = await new MapleReportBuilder(chain).build(address)
  if (output === 'graph') return printGraphOutput(report, llmProvider)

  const scored = score ? await scoreReport(report, llmProvider) : undefined
  if (output === 'json') return printJsonOutput(report, scored)

  new ReportPrinter().print(report)
  if (scored) new MarketScorePrinter().print(scored)
}
