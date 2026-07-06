// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { formatPercent, formatUsd } from '../../../lib/format'
import { assetFamily, describeCorrelation } from '../../../lib/tokens'
import type { Chain } from '../../../lib/web3/chains'
import { SparkQuerier } from '../queries/SparkQuerier'
import type {
  BorrowerPosition,
  DebtCollateralization,
  Market,
  ReserveHistory,
  ResolvedReserve,
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
  debtCollateralization: DebtCollateralization[]
}

const TOP_POSITION_LIMIT = 10

export class SparkReportBuilder {
  private readonly querier: SparkQuerier

  constructor(private readonly chain: Chain) {
    this.querier = new SparkQuerier(chain)
  }

  async build(address: string): Promise<MarketReport> {
    const reserveData = await this.querier.findReserve(address)
    if (!reserveData) throw new Error(`Reserve ${address} was not found.`)

    const [suppliers, borrowers, historicalRecords, debtCollateralization] = await Promise.all([
      this.querier.getSuppliers(reserveData.reserveId, TOP_POSITION_LIMIT),
      this.querier.getBorrowers(reserveData.reserveId, TOP_POSITION_LIMIT),
      this.querier.getHistoricalState(reserveData.reserveId),
      this.querier.getDebtCollateralization(reserveData.reserveId),
    ])
    const input: EvidenceInput = { reserveData, suppliers, borrowers, historicalRecords, debtCollateralization }

    return {
      chain: `${this.chain.network} (${this.chain.id})`,
      marketId: input.reserveData.reserveId,
      loanAsset: input.reserveData.reserve.symbol,
      loanFamily: assetFamily({ symbol: input.reserveData.reserve.symbol, tags: input.reserveData.reserve.tags }),
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
    const totalReservesUsd = this.totalReservesUsd(reserve)
    const currentBadDebtUsd = this.reserveDeficitUsd(reserve)
    const historicalBadDebtUsd = this.maxHistoricalReserveDeficitUsd(input.historicalRecords)

    return {
      totalSupplied: formatUsd(reserve.totalSupplyUsd),
      totalBorrowed: formatUsd(reserve.totalBorrowUsd),
      idleLiquidity: formatUsd(reserve.availableLiquidityUsd),
      utilizationRate: formatPercent(reserve.utilizationRate),
      supplyRate: formatPercent(reserve.supplyApy),
      borrowRate: formatPercent(reserve.borrowApy),
      totalReserves: formatUsd(totalReservesUsd),
      reserveFactor: formatPercent(reserve.reserveFactor),
      currentBadDebt: currentBadDebtUsd > 0 ? formatUsd(currentBadDebtUsd) : 'No market-specific balance-sheet deficit observed',
      historicalBadDebt: historicalBadDebtUsd > 0 ? `${formatUsd(historicalBadDebtUsd)} max observed in sampled history` : 'No sampled historical balance-sheet deficit observed',
      reserveCoverageVsBadDebt: this.formatReserveCoverageVsBadDebt(totalReservesUsd, currentBadDebtUsd),
      collaterals: this.buildMarketCollaterals(collaterals, input.debtCollateralization, reserve.symbol),
      existenceOfCapsOrIsolationMechanisms: `caps=${reserve.supplyCapUsd > 0 || reserve.borrowCapUsd > 0 ? 'yes' : 'no'}; isolationFlags=per-collateral`,
      supplyCap: this.formatCapUsage(reserve.totalSupplyUsd, reserve.supplyCapUsd),
      borrowCap: this.formatCapUsage(reserve.totalBorrowUsd, reserve.borrowCapUsd),
    }
  }

  private buildWithdrawalLiquidityEvidence(input: EvidenceInput): WithdrawalLiquidityEvidence {
    const reserve = input.reserveData.reserve
    const topSupplier = this.topBy(input.suppliers, (position) => position.supplyUsd)
    const topFiveSupplierBalance = this.topExposure(input.suppliers, 5, (position) => position.supplyUsd)
    const topTenSupplierBalance = this.topExposure(input.suppliers, 10, (position) => position.supplyUsd)

    return {
      idleLiquidity: formatUsd(reserve.availableLiquidityUsd),
      utilizationRate: formatPercent(reserve.utilizationRate),
      topFiveSupplierConcentration: formatPercent(this.ratio(topFiveSupplierBalance, reserve.totalSupplyUsd)),
      topTenSupplierConcentration: formatPercent(this.ratio(topTenSupplierBalance, reserve.totalSupplyUsd)),
      topFiveBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, reserve.totalBorrowUsd, 5, (position) => position.borrowUsd)),
      topTenBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, reserve.totalBorrowUsd, 10, (position) => position.borrowUsd)),
      historicalUtilizationAverage: this.formatHistoricalUtilizationAverage(input.historicalRecords),
      historicalUtilizationPeak: this.formatHistoricalUtilizationPeak(input.historicalRecords),
      optimalUtilization: formatPercent(reserve.optimalUsageRate),
      baseRate: formatPercent(reserve.baseVariableBorrowRate),
      slope1: formatPercent(reserve.variableRateSlope1),
      slope2: formatPercent(reserve.variableRateSlope2),
      rateResponseAtHighUtilization: this.formatRateResponseAtHighUtilization(
        reserve.utilizationRate,
        reserve.optimalUsageRate,
        reserve.baseVariableBorrowRate,
        reserve.variableRateSlope1,
        reserve.variableRateSlope2,
      ),
      largestSupplierBalanceVsIdleLiquidity: `${this.formatRatio(topSupplier ? topSupplier.supplyUsd : 0, reserve.availableLiquidityUsd)}x`,
      largestSupplierExitCoverage: formatPercent(this.ratio(reserve.availableLiquidityUsd, topSupplier ? topSupplier.supplyUsd : 0)),
      topFiveSupplierExitCoverage: formatPercent(this.ratio(reserve.availableLiquidityUsd, topFiveSupplierBalance)),
      topTenSupplierExitCoverage: formatPercent(this.ratio(reserve.availableLiquidityUsd, topTenSupplierBalance)),
      tenPercentSupplyExitCoverage: formatPercent(this.ratio(reserve.availableLiquidityUsd, reserve.totalSupplyUsd * 0.10)),
      twentyFivePercentSupplyExitCoverage: formatPercent(this.ratio(reserve.availableLiquidityUsd, reserve.totalSupplyUsd * 0.25)),
      borrowAssetsVsIdleLiquidity: `${this.formatRatio(reserve.totalBorrowUsd, reserve.availableLiquidityUsd)}x`,
    }
  }

  private buildCollateralDependencyRobustnessEvidence(input: EvidenceInput): CollateralDependencyRobustnessEvidence {
    return {
      collaterals: this.buildDependencyCollaterals(this.collateralReserves(input)),
      reserveFactor: formatPercent(input.reserveData.reserve.reserveFactor),
      historicalUtilization: this.formatHistoricalUtilization(input.historicalRecords),
    }
  }

  private buildCollateralAndLiquidityDiversificationEvidence(input: EvidenceInput): CollateralAndLiquidityDiversificationEvidence {
    const reserve = input.reserveData.reserve
    const topSupplier = this.topBy(input.suppliers, (position) => position.supplyUsd)
    const debtCollateralization = input.debtCollateralization.filter((collateral) => collateral.amountUsd > 0)
    const totalDebtCollateralization = this.sumBy(debtCollateralization, (collateral) => collateral.amountUsd)
    const topCollaterals = [...debtCollateralization].sort((left, right) => right.amountUsd - left.amountUsd).slice(0, 3)
    const topCollateralUsd = this.sumBy(topCollaterals, (collateral) => collateral.amountUsd)
    const topCollateralSymbols = topCollaterals.map((collateral) => collateral.symbol).join(', ')
    const collateralFamilyEntries = debtCollateralization.map((collateral) => {
      const market = input.reserveData.reserves.find((item) => item.symbol.toLowerCase() === collateral.symbol.toLowerCase())
      const tags = market ? market.tags : []
      return {
        family: assetFamily({ symbol: collateral.symbol, tags }),
        symbol: collateral.symbol,
        amountUsd: collateral.amountUsd,
      }
    })
    const correlatedCollaterals = input.debtCollateralization.filter((collateral) => {
      const market = input.reserveData.reserves.find((item) => item.symbol.toLowerCase() === collateral.symbol.toLowerCase())
      const tags = market ? market.tags : []
      return describeCorrelation({ symbol: collateral.symbol, tags }, { symbol: reserve.symbol, tags: reserve.tags }).isLoanCorrelated
    })
    const correlatedCollateralUsd = this.sumBy(correlatedCollaterals, (collateral) => collateral.amountUsd)
    const correlatedCollateralSymbols = correlatedCollaterals.map((collateral) => collateral.symbol).join(', ')

    return {
      concentrationInTopCollateralAssets: totalDebtCollateralization > 0 ? `${formatPercent(topCollateralUsd / totalDebtCollateralization)} (${topCollateralSymbols})` : 'Unknown',
      exposureToLoanAssetCorrelatedCollateral: totalDebtCollateralization > 0 ? `${formatPercent(correlatedCollateralUsd / totalDebtCollateralization)} (${correlatedCollateralSymbols || 'none'})` : 'Unknown',
      exposureToInternallyCorrelatedCollateralFamilies: this.formatFamilyExposure(collateralFamilyEntries, totalDebtCollateralization),
      topFiveBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, reserve.totalBorrowUsd, 5, (position) => position.borrowUsd)),
      topTenBorrowerConcentration: formatPercent(this.concentrationShare(input.borrowers, reserve.totalBorrowUsd, 10, (position) => position.borrowUsd)),
      topFiveSupplierConcentration: formatPercent(this.concentrationShare(input.suppliers, reserve.totalSupplyUsd, 5, (position) => position.supplyUsd)),
      topTenSupplierConcentration: formatPercent(this.concentrationShare(input.suppliers, reserve.totalSupplyUsd, 10, (position) => position.supplyUsd)),
      largestSupplierBalanceVsIdleLiquidity: `${this.formatRatio(topSupplier ? topSupplier.supplyUsd : 0, reserve.availableLiquidityUsd)}x`,
    }
  }

  private buildMarketCollaterals(
    collaterals: Market[],
    debtCollateralization: DebtCollateralization[],
    loanSymbol: string,
  ): CollateralSolvencyEvidence[] {
    const totalDebtCollateralization = this.sumBy(debtCollateralization, (collateral) => collateral.amountUsd)

    return collaterals.map((collateral) => {
      const correlation = describeCorrelation({ symbol: collateral.symbol, tags: collateral.tags }, { symbol: loanSymbol, tags: [] })
      const collateralization = debtCollateralization.find((item) => item.symbol.toLowerCase() === collateral.symbol.toLowerCase())
      const amountUsd = collateralization ? collateralization.amountUsd : 0

      return {
        asset: collateral.symbol,
        family: assetFamily({ symbol: collateral.symbol, tags: collateral.tags }),
        exposure: `collateral family ${correlation.collateralFamily}; loan family ${correlation.loanFamily}; correlated=${correlation.isLoanCorrelated ? 'yes' : 'no'}`,
        maximumLtv: formatPercent(collateral.ltv),
        shareOfDebt: totalDebtCollateralization > 0 ? formatPercent(amountUsd / totalDebtCollateralization) : 'Unknown',
        liquidityProfile: `liquidity=${formatUsd(collateral.availableLiquidityUsd)}; supplyCap=${formatUsd(collateral.supplyCapUsd)}; borrowCap=${formatUsd(collateral.borrowCapUsd)}`,
        liquidationThreshold: formatPercent(collateral.liquidationThreshold),
        liquidationIncentive: formatPercent(Math.max(collateral.liquidationBonus - 1, 0)),
      }
    })
  }

  private buildDependencyCollaterals(collaterals: Market[]): CollateralDependencyEvidence[] {
    return collaterals.map((collateral) => ({
      asset: collateral.symbol,
      flags: this.collateralFlags(collateral),
      family: assetFamily({ symbol: collateral.symbol, tags: collateral.tags }),
      maximumLtv: formatPercent(collateral.ltv),
      liquidationThreshold: formatPercent(collateral.liquidationThreshold),
      liquidationPenalty: formatPercent(Math.max(collateral.liquidationBonus - 1, 0)),
    }))
  }

  private collateralFlags(collateral: Market): string {
    return [
      `collateral=${collateral.usageAsCollateralEnabled ? 'yes' : 'no'}`,
      `borrowing=${collateral.borrowingEnabled ? 'yes' : 'no'}`,
      `frozen=${collateral.isFrozen ? 'yes' : 'no'}`,
      `isolated=${collateral.borrowingIsolationMode || collateral.collateralIsolationMode ? 'yes' : 'no'}`,
    ].join('; ')
  }

  private collateralReserves(input: EvidenceInput): Market[] {
    return input.reserveData.reserves
      .filter((reserve) => reserve.usageAsCollateralEnabled)
      .sort((left, right) => left.symbol.localeCompare(right.symbol))
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
    return this.sumBy(records, (record) => record.utilization) / records.length
  }

  private peakHistoricalUtilization(records: ReserveHistory[]): number {
    return records.reduce((peak, record) => Math.max(peak, record.utilization), 0)
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

  private totalReservesUsd(reserve: Market): number {
    return reserve.totalSupplyUsd - reserve.totalBorrowUsd - reserve.availableLiquidityUsd
  }

  private reserveDeficitUsd(reserve: Market): number {
    return Math.max(-this.totalReservesUsd(reserve), 0)
  }

  private maxHistoricalReserveDeficitUsd(records: ReserveHistory[]): number {
    return records.reduce((maxDeficit, record) => {
      const deficit = Math.max(record.totalBorrowUsd + record.availableLiquidityUsd - record.totalSupplyUsd, 0)
      return Math.max(maxDeficit, deficit)
    }, 0)
  }

  private formatReserveCoverageVsBadDebt(totalReservesUsd: number, currentBadDebtUsd: number): string {
    if (currentBadDebtUsd <= 0) return `totalReserves=${formatUsd(totalReservesUsd)}; no current bad debt observed`

    const reservesAvailableUsd = Math.max(totalReservesUsd, 0)
    return `${this.formatRatio(reservesAvailableUsd, currentBadDebtUsd)}x; totalReserves=${formatUsd(totalReservesUsd)}; currentBadDebt=${formatUsd(currentBadDebtUsd)}`
  }
}
