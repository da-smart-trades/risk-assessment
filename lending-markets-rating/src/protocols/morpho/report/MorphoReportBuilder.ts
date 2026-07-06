// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { formatPercent, formatUsd } from '../../../lib/format'
import { describeCollateralFamily, describeCollateralFlags, describeCorrelation } from '../../../lib/tokens'
import { wadToRatio } from '../../../lib/numbers'
import type {
  Borrower,
  HistoricalMarketState,
  Market,
  Supplier,
} from '../queries/types'
import { MorphoQuerier } from '../queries/MorphoQuerier'
import type {
  CollateralAndLiquidityDiversificationEvidence,
  CollateralDependencyRobustnessEvidence,
  ControlModifiersEvidence,
  MarketAnchorsEvidence,
  MarketSolvencyEvidence,
  MarketReport,
  WithdrawalLiquidityEvidence,
} from './types'
import type { Chain } from '../../../lib/web3/chains'

type EvidenceInput = {
  market: Market
  borrowers: Borrower[]
  suppliers: Supplier[]
  historicalState: HistoricalMarketState
  lltvRatio: number
}

const TOP_POSITION_LIMIT = 10

export class MorphoReportBuilder {
  private readonly querier: MorphoQuerier

  constructor() {
    this.querier = new MorphoQuerier()
  }

  async build(chain: Chain, marketId: string): Promise<MarketReport> {
    const [market, borrowers, suppliers, historicalState] = await Promise.all([
      this.querier.getMarket(chain, marketId),
      this.querier.getBorrowers(chain, marketId, TOP_POSITION_LIMIT),
      this.querier.getSuppliers(chain, marketId, TOP_POSITION_LIMIT),
      this.querier.getHistoricalMarket(chain, marketId),
    ])
    const lltvRatio = wadToRatio(market.lltv)
    const correlation = describeCorrelation(market.collateralAsset, market.loanAsset)
    const input = { market, borrowers, suppliers, historicalState, lltvRatio }
    const anchors = this.buildAnchors(input)
    const modifiers = this.buildControlModifiers(input)
    return {
      chain: `${chain.network} (${chain.id})`,
      marketId,
      loanAsset: market.loanAsset.symbol,
      loanFamily: correlation.loanFamily,
      anchors,
      modifiers,
    }
  }

  private buildAnchors(input: EvidenceInput): MarketAnchorsEvidence {
    return {
      marketSolvency: this.buildMarketSolvencyEvidence(input),
      withdrawalLiquidity: this.buildWithdrawalLiquidityEvidence(input),
    }
  }

  private buildControlModifiers(input: EvidenceInput): ControlModifiersEvidence {
    return {
      collateralDependencyRobustness: this.buildCollateralDependencyRobustnessEvidence(input),
      collateralAndLiquidityDiversification: this.buildCollateralAndLiquidityDiversificationEvidence(input),
    }
  }

  private buildMarketSolvencyEvidence(input: EvidenceInput): MarketSolvencyEvidence {
    const totalSupply = formatUsd(input.market.state.supplyAssetsUsd)
    const totalBorrow = formatUsd(input.market.state.borrowAssetsUsd)
    const liquidity = formatUsd(input.market.state.liquidityAssetsUsd)
    const correlation = describeCorrelation(input.market.collateralAsset, input.market.loanAsset)
    return {
      totalSupplied: totalSupply,
      totalBorrowed: totalBorrow,
      idleLiquidity: liquidity,
      utilizationRate: formatPercent(input.market.state.utilization),
      supplyRate: formatPercent(input.market.state.supplyApy),
      borrowRate: formatPercent(input.market.state.borrowApy),
      totalReserves: formatUsd(input.market.state.supplyAssetsUsd - input.market.state.borrowAssetsUsd - input.market.state.liquidityAssetsUsd),
      reserveFactor: formatPercent(input.market.state.fee),
      collateralAsset: input.market.collateralAsset.symbol,
      collateralFamily: describeCollateralFamily(input.market.collateralAsset),
      collateralExposure: `collateral family ${correlation.collateralFamily}; loan family ${correlation.loanFamily}; correlated=${correlation.isLoanCorrelated ? 'yes' : 'no'}`,
      collateralMaximumLtv: formatPercent(input.lltvRatio),
      collateralShareOfDebt: `${input.market.collateralAsset.symbol} backs ${formatPercent(1)} of market debt`,
      collateralLiquidityProfile: `listed=${input.market.collateralAsset.isListed ? 'yes' : 'no'}; tags=${input.market.collateralAsset.tags.join(', ') || 'none'}`,
      collateralLiquidationThreshold: formatPercent(input.lltvRatio),
      collateralLiquidationIncentive: formatPercent(this.calculateLiquidationPenalty(input.lltvRatio)),
      historicalBadDebtInTheMarket: formatUsd(input.market.realizedBadDebtUsd),
      existenceOfCapsOrIsolationMechanisms: 'No',
      evidenceOfSolvencyDuringPriorStressEvents: `realizedBadDebt=${formatUsd(input.market.realizedBadDebtUsd)}`,
    }
  }

  private buildWithdrawalLiquidityEvidence(input: EvidenceInput): WithdrawalLiquidityEvidence {
    const cashUsd = input.market.state.liquidityAssetsUsd
    const topSupplier = [...input.suppliers].sort((a, b) => b.supplyAssetsUsd - a.supplyAssetsUsd)[0]

    return {
      idleLiquidity: formatUsd(cashUsd),
      utilizationRate: formatPercent(input.market.state.utilization),
      topFiveSupplierConcentration: formatPercent(this.concentrationShare(input.suppliers, input.market.state.supplyAssetsUsd, 5, (supplier) => supplier.supplyAssetsUsd)),
      topTenSupplierConcentration: formatPercent(this.concentrationShare(input.suppliers, input.market.state.supplyAssetsUsd, 10, (supplier) => supplier.supplyAssetsUsd)),
      topFiveBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, input.market.state.borrowAssetsUsd, 5, (borrower) => borrower.borrowAssetsUsd)),
      topTenBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, input.market.state.borrowAssetsUsd, 10, (borrower) => borrower.borrowAssetsUsd)),
      historicalUtilizationAverage: this.formatHistoricalUtilizationAverage(input.historicalState),
      historicalUtilizationPeak: this.formatHistoricalUtilizationPeak(input.historicalState),
      largestSupplierBalanceVsIdleLiquidity: `${this.formatRatio(topSupplier?.supplyAssetsUsd ?? 0, input.market.state.liquidityAssetsUsd)}x`,
      borrowAssetsVsIdleLiquidity: `${this.formatRatio(input.market.state.borrowAssetsUsd, input.market.state.liquidityAssetsUsd)}x`,
    }
  }

  private buildCollateralDependencyRobustnessEvidence(input: EvidenceInput): CollateralDependencyRobustnessEvidence {
    return {
      collateralAsset: input.market.collateralAsset.symbol,
      collateralFlags: describeCollateralFlags(input.market.collateralAsset),
      collateralFamily: describeCollateralFamily(input.market.collateralAsset),
      collateralMaximumLtv: formatPercent(input.lltvRatio),
      collateralLiquidationThreshold: formatPercent(input.lltvRatio),
      collateralLiquidationPenalty: formatPercent(this.calculateLiquidationPenalty(input.lltvRatio)),
      reserveFactor: formatPercent(input.market.state.fee),
      historicalBadDebt: formatUsd(input.market.realizedBadDebtUsd),
      historicalUtilization: this.formatHistoricalUtilization(input.historicalState),
    }
  }

  private buildCollateralAndLiquidityDiversificationEvidence(input: EvidenceInput): CollateralAndLiquidityDiversificationEvidence {
    const topSupplier = [...input.suppliers].sort((a, b) => b.supplyAssetsUsd - a.supplyAssetsUsd)[0]
    const correlation = describeCorrelation(input.market.collateralAsset, input.market.loanAsset)

    return {
      concentrationInTopCollateralAssets: formatPercent(1),
      exposureToCorrelatedCollateral: `collateral family ${correlation.collateralFamily}; loan family ${correlation.loanFamily}; correlated=${correlation.isLoanCorrelated ? 'yes' : 'no'}`,
      topFiveBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, input.market.state.borrowAssetsUsd, 5, (borrower) => borrower.borrowAssetsUsd)),
      topTenBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, input.market.state.borrowAssetsUsd, 10, (borrower) => borrower.borrowAssetsUsd)),
      topFiveSupplierConcentration: formatPercent(this.concentrationShare(input.suppliers, input.market.state.supplyAssetsUsd, 5, (supplier) => supplier.supplyAssetsUsd)),
      topTenSupplierConcentration: formatPercent(this.concentrationShare(input.suppliers, input.market.state.supplyAssetsUsd, 10, (supplier) => supplier.supplyAssetsUsd)),
      largestSupplierBalanceVsIdleLiquidity: `${this.formatRatio(topSupplier?.supplyAssetsUsd ?? 0, input.market.state.liquidityAssetsUsd)}x`,
      historicalBadDebt: formatUsd(input.market.realizedBadDebtUsd),
    }
  }

  private concentrationShare<T>(
    entries: T[],
    total: number,
    count: number,
    valueOf: (entry: T) => number,
  ): number {
    if (!total) return 0

    return entries
      .slice()
      .sort((a, b) => valueOf(b) - valueOf(a))
      .slice(0, count)
      .reduce((sum, entry) => sum + valueOf(entry), 0) / total
  }

  private calculateLiquidationPenalty(lltvRatio: number): number {
    return 1 - lltvRatio
  }

  private formatRatio(value: number, divisor: number): number {
    if (!divisor) return 0
    return value / divisor
  }

  private formatHistoricalUtilization(state: HistoricalMarketState): string {
    if (!state.available) return 'Unknown'

    const average = formatPercent(state.averageUtilization)
    const peak = formatPercent(state.peakUtilization)
    return `avg ${average}, peak ${peak}`
  }

  private formatHistoricalUtilizationAverage(state: HistoricalMarketState): string {
    if (!state.available) return 'Unknown'
    return formatPercent(state.averageUtilization)
  }

  private formatHistoricalUtilizationPeak(state: HistoricalMarketState): string {
    if (!state.available) return 'Unknown'
    return formatPercent(state.peakUtilization)
  }
}
