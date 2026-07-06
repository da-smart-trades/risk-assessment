// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { formatPercent, formatUsd } from '../../../lib/format'
import { assetFamily, describeCorrelation } from '../../../lib/tokens'
import type { Chain } from '../../../lib/web3/chains'
import { AaveQuerier } from '../queries/AaveQuerier'
import type { ResolvedReserve } from '../queries/AaveQuerier'
import type {
  BorrowerPosition,
  LiquidationHistory,
  Reserve,
  ReserveHistory,
  SupplierPosition,
} from '../queries/types'
import type {
  CollateralAndLiquidityDiversificationEvidence,
  CollateralDependencyEvidence,
  CollateralDependencyRobustnessEvidence,
  CollateralSolvencyEvidence,
  ControlModifiersEvidence,
  MarketAnchorsEvidence,
  MarketReport,
  MarketSolvencyEvidence,
  WithdrawalLiquidityEvidence,
} from './types'

type EvidenceInput = {
  reserveData: ResolvedReserve
  suppliers: SupplierPosition[]
  borrowers: BorrowerPosition[]
  historicalRecords: ReserveHistory[]
  liquidationHistory: LiquidationHistory
}

const TOP_POSITION_LIMIT = 10

export class AaveV3ReportBuilder {
  private readonly querier: AaveQuerier

  constructor(private readonly chain: Chain) {
    this.querier = new AaveQuerier(chain)
  }

  async build(address: string): Promise<MarketReport> {
    const reserveData = await this.querier.findReserve(address)
    if (!reserveData) throw new Error(`Reserve ${address} was not found.`)

    const [suppliers, borrowers, historicalRecords, liquidationHistory] = await Promise.all([
      this.querier.getSuppliers(reserveData.reserveId, TOP_POSITION_LIMIT),
      this.querier.getBorrowers(reserveData.reserveId, TOP_POSITION_LIMIT),
      this.querier.getHistoricalState(reserveData.reserveId),
      this.querier.getLiquidationHistory(reserveData.reserveId),
    ])
    const input: EvidenceInput = { reserveData, suppliers, borrowers, historicalRecords, liquidationHistory }
    const reserve = input.reserveData.reserve
    const symbol = reserve.underlyingToken.symbol || reserve.aToken.symbol

    return {
      chain: `${this.chain.network} (${this.chain.id})`,
      marketId: input.reserveData.reserveId,
      loanAsset: symbol,
      loanFamily: assetFamily({ symbol, tags: [] }),
      anchors: this.buildAnchors(input),
      modifiers: this.buildControlModifiers(input),
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
    const reserve = input.reserveData.reserve
    const collaterals = this.collateralReserves(input)
    const borrowInfo = reserve.borrowInfo
    const totalBorrowedUsd = borrowInfo ? borrowInfo.total.usd : 0
    const availableLiquidityUsd = borrowInfo ? borrowInfo.availableLiquidity.usd : 0
    const borrowCapUsd = borrowInfo ? borrowInfo.borrowCap.usd : 0
    const totalBorrowUsd = this.sumBy(collaterals, (collateral) => collateral.borrowInfo ? collateral.borrowInfo.total.usd : 0)
    const loanSymbol = reserve.underlyingToken.symbol || reserve.aToken.symbol
    const totalReservesUsd = this.totalReservesUsd(reserve, totalBorrowedUsd, availableLiquidityUsd)
    const currentBadDebtUsd = this.reserveDeficitUsd(totalReservesUsd)

    return {
      totalSupplied: formatUsd(reserve.size.usd),
      totalBorrowed: formatUsd(totalBorrowedUsd),
      idleLiquidity: formatUsd(availableLiquidityUsd),
      utilizationRate: formatPercent(borrowInfo ? borrowInfo.utilizationRate.value : 0),
      supplyRate: formatPercent(reserve.supplyInfo.apy.value),
      borrowRate: formatPercent(borrowInfo ? borrowInfo.apy.value : 0),
      totalReserves: formatUsd(totalReservesUsd),
      reserveFactor: formatPercent(borrowInfo ? borrowInfo.reserveFactor.value : 0),
      currentBadDebt: currentBadDebtUsd > 0 ? formatUsd(currentBadDebtUsd) : 'No market-specific balance-sheet deficit observed',
      historicalBadDebt: 'Not available from current Aave reserve history query',
      underwaterAccounts: 'Not available from current Aave market-level and top-borrower queries',
      reserveCoverageVsBadDebt: this.formatReserveCoverageVsBadDebt(totalReservesUsd, currentBadDebtUsd),
      recentLiquidationVolume: `${formatUsd(input.liquidationHistory.debtRepaidUsd)} repaid across ${input.liquidationHistory.eventCount} events in the last 90 days`,
      recentDebtRepaid: formatUsd(input.liquidationHistory.debtRepaidUsd),
      recentCollateralSeized: formatUsd(input.liquidationHistory.collateralSeizedUsd),
      failedLiquidations: 'Not indexed by Aave liquidationCall events',
      badDebtAfterLiquidations: currentBadDebtUsd > 0 ? formatUsd(currentBadDebtUsd) : 'No current balance-sheet deficit observed',
      collaterals: this.buildMarketCollaterals(collaterals, totalBorrowUsd, loanSymbol, input.liquidationHistory),
      existenceOfCapsOrIsolationMechanisms: `caps=${reserve.supplyInfo.supplyCap.usd > 0 || borrowCapUsd > 0 ? 'yes' : 'no'}; isolationFlags=per-collateral`,
      supplyCap: this.formatCapUsage(reserve.size.usd, reserve.supplyInfo.supplyCap.usd),
      borrowCap: this.formatCapUsage(totalBorrowedUsd, borrowCapUsd),
    }
  }

  private buildWithdrawalLiquidityEvidence(input: EvidenceInput): WithdrawalLiquidityEvidence {
    const reserve = input.reserveData.reserve
    const borrowInfo = reserve.borrowInfo
    const availableLiquidityUsd = borrowInfo ? borrowInfo.availableLiquidity.usd : 0
    const topSupplier = this.topBy(input.suppliers, (position) => position.currentATokenBalance)
    const totalSupplied = reserve.size.usd
    const totalBorrowed = borrowInfo ? borrowInfo.total.usd : 0
    const tokenUnit = 10 ** reserve.underlyingToken.decimals
    const supplierBalanceUsd = (position: SupplierPosition) => (position.currentATokenBalance / tokenUnit) * reserve.usdExchangeRate
    const topFiveSupplierBalance = this.topExposure(input.suppliers, 5, supplierBalanceUsd)
    const topTenSupplierBalance = this.topExposure(input.suppliers, 10, supplierBalanceUsd)
    const largestSupplierBalance = topSupplier ? supplierBalanceUsd(topSupplier) : 0

    return {
      idleLiquidity: formatUsd(availableLiquidityUsd),
      utilizationRate: formatPercent(borrowInfo ? borrowInfo.utilizationRate.value : 0),
      topFiveSupplierConcentration: formatPercent(this.ratio(topFiveSupplierBalance, totalSupplied)),
      topTenSupplierConcentration: formatPercent(this.ratio(topTenSupplierBalance, totalSupplied)),
      topFiveBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, totalBorrowed, 5, (position) => (position.currentTotalDebt / tokenUnit) * reserve.usdExchangeRate)),
      topTenBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, totalBorrowed, 10, (position) => (position.currentTotalDebt / tokenUnit) * reserve.usdExchangeRate)),
      historicalUtilizationAverage: this.formatHistoricalUtilizationAverage(input.historicalRecords),
      historicalUtilizationPeak: this.formatHistoricalUtilizationPeak(input.historicalRecords),
      optimalUtilization: borrowInfo ? formatPercent(borrowInfo.optimalUsageRate.value) : 'No borrow market',
      baseRate: borrowInfo ? formatPercent(borrowInfo.baseVariableBorrowRate.value) : 'No borrow market',
      slope1: borrowInfo ? formatPercent(borrowInfo.variableRateSlope1.value) : 'No borrow market',
      slope2: borrowInfo ? formatPercent(borrowInfo.variableRateSlope2.value) : 'No borrow market',
      rateResponseAtHighUtilization: borrowInfo
        ? this.formatRateResponseAtHighUtilization(
          borrowInfo.utilizationRate.value,
          borrowInfo.optimalUsageRate.value,
          borrowInfo.baseVariableBorrowRate.value,
          borrowInfo.variableRateSlope1.value,
          borrowInfo.variableRateSlope2.value,
        )
        : 'No borrow market',
      largestSupplierBalanceVsIdleLiquidity: `${this.formatRatio(largestSupplierBalance, availableLiquidityUsd)}x`,
      largestSupplierExitCoverage: formatPercent(this.ratio(availableLiquidityUsd, largestSupplierBalance)),
      topFiveSupplierExitCoverage: formatPercent(this.ratio(availableLiquidityUsd, topFiveSupplierBalance)),
      topTenSupplierExitCoverage: formatPercent(this.ratio(availableLiquidityUsd, topTenSupplierBalance)),
      tenPercentSupplyExitCoverage: formatPercent(this.ratio(availableLiquidityUsd, totalSupplied * 0.10)),
      twentyFivePercentSupplyExitCoverage: formatPercent(this.ratio(availableLiquidityUsd, totalSupplied * 0.25)),
      borrowAssetsVsIdleLiquidity: `${this.formatRatio(borrowInfo ? borrowInfo.total.usd : 0, availableLiquidityUsd)}x`,
    }
  }

  private buildCollateralDependencyRobustnessEvidence(input: EvidenceInput): CollateralDependencyRobustnessEvidence {
    const borrowInfo = input.reserveData.reserve.borrowInfo

    return {
      collaterals: this.buildDependencyCollaterals(this.collateralReserves(input)),
      reserveFactor: formatPercent(borrowInfo ? borrowInfo.reserveFactor.value : 0),
      historicalUtilization: this.formatHistoricalUtilization(input.historicalRecords),
    }
  }

  private buildCollateralAndLiquidityDiversificationEvidence(input: EvidenceInput): CollateralAndLiquidityDiversificationEvidence {
    const reserve = input.reserveData.reserve
    const borrowInfo = reserve.borrowInfo
    const availableLiquidityUsd = borrowInfo ? borrowInfo.availableLiquidity.usd : 0
    const topSupplier = this.topBy(input.suppliers, (position) => position.currentATokenBalance)
    const totalSupplied = reserve.size.usd
    const totalBorrowed = borrowInfo ? borrowInfo.total.usd : 0
    const tokenUnit = 10 ** reserve.underlyingToken.decimals
    const loanSymbol = reserve.underlyingToken.symbol || reserve.aToken.symbol
    const collateralReserves = this.collateralReserves(input).filter((collateral) => collateral.size.usd > 0)
    const totalCollateralUsd = this.sumBy(collateralReserves, (collateral) => collateral.size.usd)
    const topCollaterals = [...collateralReserves].sort((left, right) => right.size.usd - left.size.usd).slice(0, 3)
    const topCollateralUsd = this.sumBy(topCollaterals, (collateral) => collateral.size.usd)
    const topCollateralSymbols = topCollaterals.map((collateral) => collateral.underlyingToken.symbol || collateral.aToken.symbol).join(', ')
    const collateralFamilyEntries = collateralReserves.map((collateral) => {
      const collateralSymbol = collateral.underlyingToken.symbol || collateral.aToken.symbol
      return {
        family: assetFamily({ symbol: collateralSymbol, tags: [] }),
        symbol: collateralSymbol,
        amountUsd: collateral.size.usd,
      }
    })
    const correlatedCollaterals = collateralReserves.filter((collateral) => {
      const collateralSymbol = collateral.underlyingToken.symbol || collateral.aToken.symbol
      return describeCorrelation({ symbol: collateralSymbol, tags: [] }, { symbol: loanSymbol, tags: [] }).isLoanCorrelated
    })
    const correlatedCollateralUsd = this.sumBy(correlatedCollaterals, (collateral) => collateral.size.usd)
    const correlatedCollateralSymbols = correlatedCollaterals.map((collateral) => collateral.underlyingToken.symbol || collateral.aToken.symbol).join(', ')

    return {
      concentrationInTopCollateralAssets: totalCollateralUsd > 0 ? `${formatPercent(topCollateralUsd / totalCollateralUsd)} (${topCollateralSymbols})` : 'Unknown',
      exposureToLoanAssetCorrelatedCollateral: totalCollateralUsd > 0 ? `${formatPercent(correlatedCollateralUsd / totalCollateralUsd)} (${correlatedCollateralSymbols || 'none'})` : 'Unknown',
      exposureToInternallyCorrelatedCollateralFamilies: this.formatFamilyExposure(collateralFamilyEntries, totalCollateralUsd),
      topFiveBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, totalBorrowed, 5, (position) => (position.currentTotalDebt / tokenUnit) * reserve.usdExchangeRate)),
      topTenBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, totalBorrowed, 10, (position) => (position.currentTotalDebt / tokenUnit) * reserve.usdExchangeRate)),
      topFiveSupplierConcentration: formatPercent(this.concentrationShare(input.suppliers, totalSupplied, 5, (position) => (position.currentATokenBalance / tokenUnit) * reserve.usdExchangeRate)),
      topTenSupplierConcentration: formatPercent(this.concentrationShare(input.suppliers, totalSupplied, 10, (position) => (position.currentATokenBalance / tokenUnit) * reserve.usdExchangeRate)),
      largestSupplierBalanceVsIdleLiquidity: `${this.formatRatio(topSupplier ? (topSupplier.currentATokenBalance / tokenUnit) * reserve.usdExchangeRate : 0, availableLiquidityUsd)}x`,
    }
  }

  private buildMarketCollaterals(
    collaterals: Reserve[],
    totalBorrowUsd: number,
    loanSymbol: string,
    liquidationHistory: LiquidationHistory,
  ): CollateralSolvencyEvidence[] {
    return collaterals.map((collateral) => this.buildMarketCollateral(collateral, totalBorrowUsd, loanSymbol, liquidationHistory))
  }

  private buildDependencyCollaterals(collaterals: Reserve[]): CollateralDependencyEvidence[] {
    return collaterals.map((collateral) => this.buildDependencyCollateral(collateral))
  }

  private buildMarketCollateral(
    collateral: Reserve,
    totalBorrowUsd: number,
    loanSymbol: string,
    liquidationHistory: LiquidationHistory,
  ): CollateralSolvencyEvidence {
    const symbol = collateral.underlyingToken.symbol || collateral.aToken.symbol
    const borrowInfo = collateral.borrowInfo
    const totalBorrowedUsd = borrowInfo ? borrowInfo.total.usd : 0
    const availableLiquidityUsd = borrowInfo ? borrowInfo.availableLiquidity.usd : 0
    const borrowCapUsd = borrowInfo ? borrowInfo.borrowCap.usd : 0
    const correlation = describeCorrelation({ symbol: symbol, tags: [] }, { symbol: loanSymbol, tags: [] })

    return {
      asset: symbol,
      family: assetFamily({ symbol, tags: [] }),
      exposure: `collateral family ${correlation.collateralFamily}; loan family ${correlation.loanFamily}; correlated=${correlation.isLoanCorrelated ? 'yes' : 'no'}`,
      maximumLtv: formatPercent(collateral.supplyInfo.maxLTV.value),
      shareOfBorrowPool: totalBorrowUsd > 0 ? formatPercent(totalBorrowedUsd / totalBorrowUsd) : 'Unknown',
      protocolLiquidity: `available=${formatUsd(availableLiquidityUsd)}; supplyCap=${formatUsd(collateral.supplyInfo.supplyCap.usd)}; borrowCap=${formatUsd(borrowCapUsd)}`,
      marketLiquidityForLiquidations: 'Not sourced from Aave market/subgraph data',
      historicalLiquidationPerformance: this.formatCollateralLiquidationPerformance(symbol, liquidationHistory),
      liquidationVenueDepth: 'External DEX/CEX depth not measured by current sources',
      liquidationThreshold: formatPercent(collateral.supplyInfo.liquidationThreshold.value),
      liquidationIncentive: formatPercent(collateral.supplyInfo.liquidationBonus.value),
    }
  }

  private buildDependencyCollateral(collateral: Reserve): CollateralDependencyEvidence {
    const symbol = collateral.underlyingToken.symbol || collateral.aToken.symbol

    return {
      asset: symbol,
      flags: this.collateralFlags(collateral),
      family: assetFamily({ symbol, tags: [] }),
      maximumLtv: formatPercent(collateral.supplyInfo.maxLTV.value),
      liquidationThreshold: formatPercent(collateral.supplyInfo.liquidationThreshold.value),
      liquidationPenalty: formatPercent(collateral.supplyInfo.liquidationBonus.value),
    }
  }

  private collateralFlags(collateral: Reserve): string {
    return [
      `collateral=${collateral.supplyInfo.canBeCollateral ? 'yes' : 'no'}`,
      `borrowing=${collateral.borrowInfo && collateral.borrowInfo.borrowingState === 'ENABLED' ? 'yes' : 'no'}`,
      `frozen=${collateral.isFrozen ? 'yes' : 'no'}`,
      'isolated=no',
    ].join('; ')
  }

  private collateralReserves(input: EvidenceInput): Reserve[] {
    return input.reserveData.market.reserves
      .filter((reserve) => Boolean(reserve.supplyInfo.canBeCollateral))
      .sort((left, right) => {
        const leftSymbol = left.underlyingToken.symbol || left.aToken.symbol
        const rightSymbol = right.underlyingToken.symbol || right.aToken.symbol
        return leftSymbol.localeCompare(rightSymbol)
      })
  }

  private formatHistoricalUtilization(records: ReserveHistory[]): string {
    if (records.length === 0) return 'Unknown'
    return `avg ${formatPercent(this.averageHistoricalUtilization(records))}, peak ${formatPercent(this.peakHistoricalUtilization(records))}`
  }

  private formatHistoricalUtilizationAverage(records: ReserveHistory[]): string {
    if (records.length === 0) return 'Unknown'
    return formatPercent(this.averageHistoricalUtilization(records))
  }

  private formatHistoricalUtilizationPeak(records: ReserveHistory[]): string {
    if (records.length === 0) return 'Unknown'
    return formatPercent(this.peakHistoricalUtilization(records))
  }

  private averageHistoricalUtilization(records: ReserveHistory[]): number {
    return this.sumBy(records, (record) => (
      this.calculateUtilization(record.availableLiquidity, record.totalCurrentVariableDebt, record.totalPrincipalStableDebt)
    )) / records.length
  }

  private peakHistoricalUtilization(records: ReserveHistory[]): number {
    return records.reduce((peak, record) => (
      Math.max(peak, this.calculateUtilization(record.availableLiquidity, record.totalCurrentVariableDebt, record.totalPrincipalStableDebt))
    ), 0)
  }

  private calculateUtilization(availableLiquidity: number, totalVariableDebt: number, totalStableDebt: number): number {
    const totalBorrowed = totalVariableDebt + totalStableDebt
    const denominator = totalBorrowed + Math.max(availableLiquidity, 0)
    if (denominator <= 0) return 0
    return totalBorrowed / denominator
  }

  private concentrationShare<T>(entries: T[], total: number, count: number, valueOf: (entry: T) => number): number {
    if (!total) return 0
    return this.topExposure(entries, count, valueOf) / total
  }

  private topExposure<T>(entries: T[], count: number, valueOf: (entry: T) => number): number {
    return entries
      .slice()
      .sort((left, right) => valueOf(right) - valueOf(left))
      .slice(0, count)
      .reduce((sum, entry) => sum + valueOf(entry), 0)
  }

  private formatFamilyExposure(
    entries: { family: string; symbol: string; amountUsd: number }[],
    totalUsd: number,
  ): string {
    if (totalUsd <= 0) return 'Unknown'

    const exposures = new Map<string, { amountUsd: number; symbols: Set<string> }>()

    for (const entry of entries) {
      const exposure = exposures.get(entry.family) ?? { amountUsd: 0, symbols: new Set<string>() }
      exposure.amountUsd += entry.amountUsd
      exposure.symbols.add(entry.symbol)
      exposures.set(entry.family, exposure)
    }

    return [...exposures.entries()]
      .sort(([, left], [, right]) => right.amountUsd - left.amountUsd)
      .map(([family, exposure]) => `${family}=${formatPercent(exposure.amountUsd / totalUsd)} (${[...exposure.symbols].join(', ')})`)
      .join('; ')
  }

  private topBy<T>(items: T[], valueOf: (item: T) => number): T | undefined {
    if (items.length === 0) return undefined
    return [...items].sort((left, right) => valueOf(right) - valueOf(left))[0]
  }

  private sumBy<T>(items: T[], valueOf: (item: T) => number): number {
    return items.reduce((sum, item) => sum + valueOf(item), 0)
  }

  private formatRatio(value: number, divisor: number): number {
    return this.ratio(value, divisor)
  }

  private ratio(value: number, divisor: number): number {
    if (!divisor) return 0
    return value / divisor
  }

  private formatCapUsage(usedUsd: number, capUsd: number): string {
    if (capUsd <= 0) return 'uncapped'

    return `${formatUsd(capUsd)}; used=${formatPercent(usedUsd / capUsd)}; headroom=${formatUsd(Math.max(capUsd - usedUsd, 0))}`
  }

  private formatCollateralLiquidationPerformance(symbol: string, liquidationHistory: LiquidationHistory): string {
    const stats = liquidationHistory.collateralStats.find((collateral) => collateral.collateralSymbol.toLowerCase() === symbol.toLowerCase())
    if (!stats) return 'No recent liquidations with this collateral in the last 90 days'

    return `${stats.eventCount} events; debtRepaid=${formatUsd(stats.debtRepaidUsd)}; collateralSeized=${formatUsd(stats.collateralSeizedUsd)}`
  }

  private formatRateResponseAtHighUtilization(
    currentUtilization: number,
    optimalUtilization: number,
    baseRate: number,
    slope1: number,
    slope2: number,
  ): string {
    const kinkStatus = currentUtilization >= optimalUtilization
      ? `above optimal by ${formatPercent(currentUtilization - optimalUtilization)}`
      : `below optimal by ${formatPercent(optimalUtilization - currentUtilization)}`

    return `${kinkStatus}; borrowRateAt95=${formatPercent(this.variableBorrowRate(0.95, optimalUtilization, baseRate, slope1, slope2))}; borrowRateAt100=${formatPercent(this.variableBorrowRate(1, optimalUtilization, baseRate, slope1, slope2))}`
  }

  private variableBorrowRate(
    utilization: number,
    optimalUtilization: number,
    baseRate: number,
    slope1: number,
    slope2: number,
  ): number {
    if (optimalUtilization <= 0) return baseRate + slope1 + slope2
    if (utilization <= optimalUtilization) return baseRate + utilization / optimalUtilization * slope1

    return baseRate + slope1 + (utilization - optimalUtilization) / (1 - optimalUtilization) * slope2
  }

  private totalReservesUsd(reserve: Reserve, totalBorrowedUsd: number, availableLiquidityUsd: number): number {
    return reserve.size.usd - totalBorrowedUsd - availableLiquidityUsd
  }

  private reserveDeficitUsd(totalReservesUsd: number): number {
    return Math.max(-totalReservesUsd, 0)
  }

  private formatReserveCoverageVsBadDebt(totalReservesUsd: number, currentBadDebtUsd: number): string {
    if (currentBadDebtUsd <= 0) return `totalReserves=${formatUsd(totalReservesUsd)}; no current bad debt observed`

    const reservesAvailableUsd = Math.max(totalReservesUsd, 0)
    return `${this.formatRatio(reservesAvailableUsd, currentBadDebtUsd)}x; totalReserves=${formatUsd(totalReservesUsd)}; currentBadDebt=${formatUsd(currentBadDebtUsd)}`
  }
}
