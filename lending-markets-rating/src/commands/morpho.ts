// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Command } from 'commander'
import { ReportPrinter } from '../lib/reports/ReportPrinter'
import { MarketScorePrinter } from '../lib/scoring/MarketScorePrinter'
import { DEFAULT_LLM_PROVIDER, normalizeLlmProvider, type LlmProvider } from '../lib/llms/providers'
import { MorphoReportBuilder } from '../protocols/morpho/report/MorphoReportBuilder'
import { parseChainId, requireChain } from '../lib/web3/chains'
import { normalizeOutput, printGraphOutput, printJsonOutput, scoreReport, type OutputFormat } from '../lib/commands'

export function registerMorphoCommand(program: Command): void {
  program
    .command('morpho')
    .description('Fetch and print a Morpho market report')
    .option('--output <format>', 'output format: text, json or graph', 'text')
    .option('--score', 'score the report using the selected llm provider')
    .option('--llm <provider>', 'llm provider: claude or openai', DEFAULT_LLM_PROVIDER)
    .argument('<chain-id>')
    .argument('<market-id>')
    .action(async (
      chainId: string,
      marketId: string,
      options: { output: string; score?: boolean; llm?: string },
    ) => {
      const output = normalizeOutput(options.output)
      const score = Boolean(options.score)
      const llmProvider = output === 'graph' || score ? normalizeLlmProvider(options.llm) : DEFAULT_LLM_PROVIDER
      await runMorphoCommand(
        parseChainId(chainId),
        marketId,
        output,
        score,
        llmProvider,
      )
    })
}

async function runMorphoCommand(
  chainId: number,
  marketId: string,
  output: OutputFormat,
  score: boolean,
  llmProvider: LlmProvider,
): Promise<void> {
  const chain = requireChain(chainId)
  const report = await new MorphoReportBuilder().build(chain, marketId)
  if (output === 'graph') return printGraphOutput(report, llmProvider)

  const scored = score ? await scoreReport(report, llmProvider) : undefined
  if (output === 'json') return printJsonOutput(report, scored)

  new ReportPrinter().print(report)
  if (scored) new MarketScorePrinter().print(scored)
}
