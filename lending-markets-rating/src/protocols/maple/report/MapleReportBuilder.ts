// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { formatPercent } from '../../../lib/format'
import { currentUnixTimestampSeconds, SECONDS_PER_DAY } from '../../../lib/time'
import type { Chain } from '../../../lib/web3/chains'
import { MapleQuerier } from '../queries/MapleQuerier'
import type { LpPosition, MaplePool, OpenTermLoan, StrategyAccounting } from '../queries/types'
import type {
  LoanBookDiversificationEvidence,
  LoanPerformanceImpairmentEvidence,
  MapleAnchorsEvidence,
  MapleModifiersEvidence,
  MaplePoolReport,
  ManagerTrackRecordEvidence,
  PoolAssetCoverageEvidence,
  RecoveryRobustnessEvidence,
  RedemptionLiquidityCoverageEvidence,
  StrategyExposureEvidence,
  StrategyValuationEvidence,
} from './types'

type EvidenceInput = {
  pool: MaplePool
}

const MIN_MATERIAL_STRATEGY_EXPOSURE = 1

export class MapleReportBuilder {
  private readonly querier: MapleQuerier

  constructor(private readonly chain: Chain) {
    this.querier = new MapleQuerier(chain)
  }

  async build(address: string): Promise<MaplePoolReport> {
    const pool = await this.querier.findPool(address)
    if (!pool) throw new Error(`Maple pool ${address} was not found.`)

    const input: EvidenceInput = { pool }

    return {
      chain: `${this.chain.network} (${this.chain.id})`,
      marketId: pool.id,
      poolName: pool.name,
      poolSymbol: pool.symbol,
      asset: pool.assetSymbol,
      anchors: this.buildAnchors(input),
      modifiers: this.buildModifiers(input),
    }
  }

  private buildAnchors(input: EvidenceInput): MapleAnchorsEvidence {
    return {
      poolAssetCoverage: this.buildPoolAssetCoverageEvidence(input),
      redemptionLiquidityCoverage: this.buildRedemptionLiquidityCoverageEvidence(input),
      loanPerformanceImpairment: this.buildLoanPerformanceImpairmentEvidence(input),
    }
  }

  private buildModifiers(input: EvidenceInput): MapleModifiersEvidence {
    return {
      recoveryRobustness: this.buildRecoveryRobustnessEvidence(input),
      loanBookDiversification: this.buildLoanBookDiversificationEvidence(input),
      managerTrackRecord: this.buildManagerTrackRecordEvidence(input),
    }
  }

  private buildPoolAssetCoverageEvidence(input: EvidenceInput): PoolAssetCoverageEvidence {
    const pool = input.pool
    const sharePriceHistory = pool.sharePriceHistory.length > 0 ? pool.sharePriceHistory : [{ timestamp: 0, sharePrice: pool.sharePrice }]
    const allTimeHighSharePrice = sharePriceHistory.reduce((max, point) => Math.max(max, point.sharePrice), 0)
    const sharePriceDrawdown = this.maxSharePriceDrawdown(sharePriceHistory)
    const sharePriceDeclines = this.sharePriceDeclines(sharePriceHistory)

    return {
      totalAssets: this.formatAsset(pool.totalAssets, pool.assetSymbol),
      totalSupply: this.formatShares(pool.totalSupply, pool.symbol),
      currentSharePrice: this.formatAsset(pool.sharePrice, pool.assetSymbol),
      allTimeHighSharePrice: `${this.formatAsset(allTimeHighSharePrice, pool.assetSymbol)} sampled`,
      sharePriceMaxDrawdown: sharePriceDrawdown.maxDrawdown > 0
        ? `${formatPercent(sharePriceDrawdown.maxDrawdown)} (${this.formatAsset(sharePriceDrawdown.peakSharePrice, pool.assetSymbol)} peak to ${this.formatAsset(sharePriceDrawdown.troughSharePrice, pool.assetSymbol)} trough)`
        : '0.00% (no sampled drawdown)',
      historicalSharePriceDeclines: `${sharePriceHistory.length} samples; ${sharePriceDeclines.count} sampled declines; largest interval decline ${formatPercent(sharePriceDeclines.largestDecline)}`,
      sharePriceRecoveryEvidence: this.formatSharePriceRecovery(pool.sharePrice, allTimeHighSharePrice, sharePriceDrawdown.maxDrawdown),
      principalOutstanding: this.formatAsset(pool.principalOutstanding, pool.assetSymbol),
      assetsUnderManagement: this.formatAsset(pool.assetsUnderManagement, pool.assetSymbol),
      availableLiquidity: this.formatAsset(pool.availableLiquidity, pool.assetSymbol),
      accruedInterest: this.formatAsset(pool.accruedInterest, pool.assetSymbol),
      unrealizedLosses: this.formatAsset(pool.unrealizedLosses, pool.assetSymbol),
      poolDelegateCover: `${this.formatAsset(pool.poolDelegateCover, pool.assetSymbol)} (${pool.poolDelegateCoverAddress})`,
      hasSufficientCover: pool.hasSufficientCover ? 'yes' : 'no',
      liquidityCap: this.formatAsset(pool.liquidityCap, pool.assetSymbol),
      accountingAssetCoverageRatio: `${this.formatRatio(pool.totalAssets, pool.totalSupplyValue)}x`,
      lossCoverageRatio: this.formatLossCoverage(pool.poolDelegateCover, pool.unrealizedLosses),
      assetValuationMethod: 'pool totalAssets() combines exact on-chain asset balance with strategy-reported assetsUnderManagement()',
      assetMarkSource: `pool=${pool.id}; poolManager=${pool.managerAddress}; strategy contracts=${pool.strategies.length}`,
      assetFairValueVsBookValue: `totalAssets=${this.formatAsset(pool.totalAssets, pool.assetSymbol)}; strategyAUM=${this.formatAsset(pool.assetsUnderManagement, pool.assetSymbol)}; availableLiquidity=${this.formatAsset(pool.availableLiquidity, pool.assetSymbol)}; unrealizedLosses=${this.formatAsset(pool.unrealizedLosses, pool.assetSymbol)}`,
      strategyValuation: this.buildStrategyValuationEvidence(pool),
    }
  }

  private buildStrategyValuationEvidence(pool: MaplePool): StrategyValuationEvidence[] {
    return pool.strategies
      .slice()
      .sort((left, right) => this.strategyExposure(right) - this.strategyExposure(left))
      .map((strategy) => ({
        strategyAddress: strategy.address,
        strategyType: strategy.profile.type,
        exposure: this.formatAsset(this.strategyExposure(strategy), pool.assetSymbol),
        valuationMethod: this.strategyValuationMethod(strategy),
        markSource: this.strategyMarkSource(strategy),
      }))
  }

  private buildRedemptionLiquidityCoverageEvidence(input: EvidenceInput): RedemptionLiquidityCoverageEvidence {
    const pool = input.pool
    const largestLp = this.topBy(pool.topLpPositions, (position) => position.availableBalance)
    const now = currentUnixTimestampSeconds()
    const activeLoans = pool.openTermLoans.filter((loan) => loan.principalOwed > 0)
    const calledLoans = activeLoans.filter((loan) => loan.isCalled)
    const calledLoanLiquidityDueWithinDays = (days: number) => this.calledLoanLiquidityDueWithinDays(calledLoans, now, days)
    const next7DaysLiquidity = calledLoanLiquidityDueWithinDays(7)
    const next30DaysLiquidity = calledLoanLiquidityDueWithinDays(30)
    const next90DaysLiquidity = calledLoanLiquidityDueWithinDays(90)
    const calledPrincipal = this.sumBy(calledLoans, (loan) => loan.principalOwed)
    const strategyWithdrawalDelay = this.formatMaterialStrategyWithdrawalDelay(pool.strategies)

    return {
      availableLiquidity: this.formatAsset(pool.availableLiquidity, pool.assetSymbol),
      totalAssets: this.formatAsset(pool.totalAssets, pool.assetSymbol),
      availableLiquidityRatio: formatPercent(this.ratio(pool.availableLiquidity, pool.totalAssets)),
      openTermLoanCount: String(pool.openTermLoanCount),
      activeOpenTermLoanCount: String(activeLoans.length),
      calledLoanCount: String(calledLoans.length),
      calledPrincipal: this.formatAsset(calledPrincipal, pool.assetSymbol),
      next7DaysExpectedLiquidity: this.formatAsset(next7DaysLiquidity, pool.assetSymbol),
      next30DaysExpectedLiquidity: this.formatAsset(next30DaysLiquidity, pool.assetSymbol),
      next90DaysExpectedLiquidity: this.formatAsset(next90DaysLiquidity, pool.assetSymbol),
      weightedAverageNextPaymentDays: this.formatDays(this.weightedAverageDaysUntil(activeLoans, now)),
      maximumNextPaymentDays: this.formatDays(this.maximumDaysUntil(activeLoans, now, (loan) => loan.nextPaymentDue)),
      weightedAveragePaymentIntervalDays: this.formatDays(this.weightedAverage(activeLoans, (loan) => loan.paymentIntervalDays, (loan) => loan.principalOwed)),
      maximumPaymentIntervalDays: this.formatDays(Math.max(...activeLoans.map((loan) => loan.paymentIntervalDays), 0)),
      liquidityRecallPeriod: 'open-term loans; principal is not fixed-maturity liquidity unless loans are called',
      strategyWithdrawalDelay,
      largestLp30DayExitCoverage: largestLp ? formatPercent(this.ratio(pool.availableLiquidity + next30DaysLiquidity, largestLp.availableBalance)) : 'No LP positions indexed',
      largestLp90DayExitCoverage: largestLp ? formatPercent(this.ratio(pool.availableLiquidity + next90DaysLiquidity, largestLp.availableBalance)) : 'No LP positions indexed',
      withdrawalQueue: `${this.formatAsset(pool.pendingRedemptions, pool.assetSymbol)}; next=${pool.nextWithdrawalRequest}`,
      pendingRedemptions: this.formatAsset(pool.pendingRedemptions, pool.assetSymbol),
      lockedShares: this.formatShares(pool.withdrawalQueueShares, pool.symbol),
      liquidityCap: this.formatAsset(pool.liquidityCap, pool.assetSymbol),
      remainingCapacityToLiquidityCap: this.formatAsset(pool.liquidityCapacityRemaining, pool.assetSymbol),
      lpPositionCount: String(pool.lpPositionCount),
      largestLpBalance: largestLp ? this.formatLpPosition(largestLp, pool.assetSymbol) : 'No LP positions indexed',
      topFiveLpConcentration: this.formatTopLpConcentration(pool),
      liquidityCoverageRatio: this.formatCoverage(pool.availableLiquidity, pool.pendingRedemptions),
      queueCoverageRatio: this.formatCoverage(pool.availableLiquidity, pool.pendingRedemptions),
      largestLpBalanceVsAvailableLiquidity: largestLp ? `${this.formatRatio(largestLp.availableBalance, pool.availableLiquidity)}x` : 'No LP positions indexed',
      largestLpExitCoverage: largestLp ? formatPercent(this.ratio(pool.availableLiquidity, largestLp.availableBalance)) : 'No LP positions indexed',
    }
  }

  private buildLoanPerformanceImpairmentEvidence(input: EvidenceInput): LoanPerformanceImpairmentEvidence {
    const pool = input.pool
    const strategiesWithPrincipalOutstanding = pool.strategies.filter((strategy) => strategy.principalOut > 0).length
    const now = currentUnixTimestampSeconds()
    const activeLoans = pool.openTermLoans.filter((loan) => loan.principalOwed > 0)
    const calledLoans = activeLoans.filter((loan) => loan.isCalled)
    const impairedLoans = activeLoans.filter((loan) => loan.isImpaired)
    const pastDueLoans = activeLoans.filter((loan) => this.isPastDue(loan, now))
    const lateLoans = pastDueLoans.filter((loan) => !loan.isImpaired)
    const nonPerformingPrincipal = this.sumBy(
      activeLoans.filter((loan) => loan.isImpaired || this.isPastDue(loan, now)),
      (loan) => loan.principalOwed,
    )

    return {
      strategyCount: String(pool.strategies.length),
      strategiesWithPrincipalOutstanding: String(strategiesWithPrincipalOutstanding),
      activeLoanCount: String(activeLoans.length),
      calledLoanCount: String(calledLoans.length),
      impairedLoanCount: String(impairedLoans.length),
      lateLoanCount: String(lateLoans.length),
      totalPrincipalOutstanding: this.formatAsset(pool.principalOutstanding, pool.assetSymbol),
      assetsUnderManagement: this.formatAsset(pool.assetsUnderManagement, pool.assetSymbol),
      accruedInterest: this.formatAsset(pool.accruedInterest, pool.assetSymbol),
      calledPrincipal: this.formatAsset(this.sumBy(calledLoans, (loan) => loan.principalOwed), pool.assetSymbol),
      pastDuePrincipal: this.formatAsset(this.sumBy(pastDueLoans, (loan) => loan.principalOwed), pool.assetSymbol),
      latePrincipal: this.formatAsset(this.sumBy(lateLoans, (loan) => loan.principalOwed), pool.assetSymbol),
      impairedPrincipal: this.formatAsset(this.sumBy(impairedLoans, (loan) => loan.principalOwed), pool.assetSymbol),
      nonPerformingLoanRatio: formatPercent(this.ratio(nonPerformingPrincipal, pool.principalOutstanding)),
      weightedAverageDaysPastDue: this.formatDays(this.weightedAverageDaysPastDue(pastDueLoans, now)),
      impairmentCoverage: this.formatLossCoverage(pool.poolDelegateCover, pool.unrealizedLosses),
    }
  }

  private buildRecoveryRobustnessEvidence(input: EvidenceInput): RecoveryRobustnessEvidence {
    const pool = input.pool

    return {
      poolDelegateCoverContract: pool.poolDelegateCoverAddress,
      poolDelegateCoverBalance: this.formatAsset(pool.poolDelegateCover, pool.assetSymbol),
      hasSufficientCover: pool.hasSufficientCover ? 'yes' : 'no',
      currentUnrealizedLosses: this.formatAsset(pool.unrealizedLosses, pool.assetSymbol),
      currentRealizedLosses: this.formatAsset(pool.realizedLosses, pool.assetSymbol),
      unrealizedLossesToPrincipal: formatPercent(this.ratio(pool.unrealizedLosses, pool.principalOutstanding)),
      lossCoverageRatio: this.formatLossCoverage(pool.poolDelegateCover, pool.unrealizedLosses),
      recoveryProcessTransparency: `poolManager=${pool.managerAddress}; strategies=${pool.strategies.length}; coverContract=${pool.poolDelegateCoverAddress}`,
    }
  }

  private buildLoanBookDiversificationEvidence(input: EvidenceInput): LoanBookDiversificationEvidence {
    const pool = input.pool
    const activeStrategies = pool.strategies.filter((strategy) => this.strategyExposure(strategy) >= MIN_MATERIAL_STRATEGY_EXPOSURE)
    const largestStrategy = this.topBy(activeStrategies, (strategy) => this.strategyExposure(strategy))
    const topFiveStrategyExposure = this.topExposure(activeStrategies, 5, (strategy) => this.strategyExposure(strategy))
    const activeLoans = pool.openTermLoans.filter((loan) => loan.principalOwed > 0)
    const borrowerExposures = this.borrowerExposures(activeLoans)
    const totalBorrowerExposure = this.sumBy(borrowerExposures, (borrower) => borrower.exposure)
    const largestBorrower = borrowerExposures[0]
    const topFiveBorrowerExposure = this.topExposure(borrowerExposures, 5, (borrower) => borrower.exposure)
    const topTenBorrowerExposure = this.topExposure(borrowerExposures, 10, (borrower) => borrower.exposure)

    return {
      strategyCount: String(pool.strategies.length),
      activeStrategyCount: String(activeStrategies.length),
      largestStrategyExposure: largestStrategy ? this.formatStrategyExposure(largestStrategy, pool.assetSymbol) : `0 ${pool.assetSymbol}`,
      largestStrategyExposureRatio: formatPercent(this.ratio(largestStrategy ? this.strategyExposure(largestStrategy) : 0, pool.assetsUnderManagement)),
      topFiveStrategyExposure: this.formatAsset(topFiveStrategyExposure, pool.assetSymbol),
      topFiveStrategyExposureRatio: formatPercent(this.ratio(topFiveStrategyExposure, pool.assetsUnderManagement)),
      strategyHHI: this.formatHhi(activeStrategies, pool.assetsUnderManagement, (strategy) => this.strategyExposure(strategy)),
      strategyConcentration: this.formatStrategyConcentration(activeStrategies, pool.assetSymbol),
      strategyExposures: this.buildStrategyExposureEvidence(pool),
      activeLoanCount: String(activeLoans.length),
      borrowerCount: String(borrowerExposures.length),
      largestBorrowerExposure: largestBorrower ? this.formatBorrowerExposure(largestBorrower, pool.assetSymbol) : `0 ${pool.assetSymbol}`,
      largestBorrowerExposureRatio: formatPercent(this.ratio(largestBorrower?.exposure ?? 0, totalBorrowerExposure)),
      topFiveBorrowerExposure: this.formatAsset(topFiveBorrowerExposure, pool.assetSymbol),
      topFiveBorrowerExposureRatio: formatPercent(this.ratio(topFiveBorrowerExposure, totalBorrowerExposure)),
      topTenBorrowerExposure: this.formatAsset(topTenBorrowerExposure, pool.assetSymbol),
      topTenBorrowerExposureRatio: formatPercent(this.ratio(topTenBorrowerExposure, totalBorrowerExposure)),
      borrowerHHI: this.formatHhi(borrowerExposures, totalBorrowerExposure, (borrower) => borrower.exposure),
    }
  }

  private buildStrategyExposureEvidence(pool: MaplePool): StrategyExposureEvidence[] {
    return pool.strategies
      .slice()
      .sort((left, right) => this.strategyExposure(right) - this.strategyExposure(left))
      .map((strategy) => ({
        strategyAddress: strategy.address,
        strategyName: strategy.profile.name,
        strategyType: strategy.profile.type,
        protocolOrCounterparty: strategy.profile.protocolOrCounterparty,
        exposure: this.formatAsset(this.strategyExposure(strategy), pool.assetSymbol),
        exposureRatio: formatPercent(this.ratio(this.strategyExposure(strategy), pool.assetsUnderManagement)),
        asset: `${pool.assetSymbol} (${strategy.assetAddress})`,
        liquidityTerms: strategy.profile.liquidityTerms,
        maturityProfile: strategy.profile.maturityProfile,
        collateralization: strategy.profile.collateralization,
        riskCategory: strategy.profile.riskCategory,
        externalDependencies: strategy.profile.externalDependencies,
      }))
  }

  private buildManagerTrackRecordEvidence(input: EvidenceInput): ManagerTrackRecordEvidence {
    const pool = input.pool

    return {
      poolDelegateAddress: pool.poolDelegateAddress,
      poolManagerAddress: pool.managerAddress,
      activePrincipalManaged: this.formatAsset(pool.principalOutstanding, pool.assetSymbol),
      activeAssetsManaged: this.formatAsset(pool.assetsUnderManagement, pool.assetSymbol),
      realizedLosses: this.formatAsset(pool.realizedLosses, pool.assetSymbol),
      unrealizedLosses: this.formatAsset(pool.unrealizedLosses, pool.assetSymbol),
      previousUnrealizedLosses: this.formatAsset(pool.previousUnrealizedLosses, pool.assetSymbol),
      realizedLossesToActivePrincipal: formatPercent(this.ratio(pool.realizedLosses, pool.principalOutstanding)),
    }
  }

  private formatAsset(value: number, symbol: string): string {
    return `${this.formatNumber(value)} ${symbol}`
  }

  private formatShares(value: number, symbol: string): string {
    return `${this.formatNumber(value)} ${symbol}`
  }

  private formatNumber(value: number): string {
    return new Intl.NumberFormat('en-US', {
      maximumFractionDigits: Math.abs(value) >= 1_000 ? 0 : 6,
    }).format(value)
  }

  private formatCoverage(value: number, divisor: number): string {
    if (!divisor) return 'No pending redemptions'
    return `${this.formatRatio(value, divisor)}x`
  }

  private formatLossCoverage(cover: number, losses: number): string {
    if (!losses) return 'No current unrealized losses'
    return `${this.formatRatio(cover, losses)}x`
  }

  private formatTopLpConcentration(pool: MaplePool): string {
    const topFiveBalance = pool.topLpPositions.reduce((sum, position) => sum + position.availableBalance, 0)
    return `${formatPercent(this.ratio(topFiveBalance, pool.totalAssets))} (${this.formatAsset(topFiveBalance, pool.assetSymbol)})`
  }

  private formatLpPosition(position: LpPosition, symbol: string): string {
    return `${this.formatAsset(position.availableBalance, symbol)} (${position.account})`
  }

  private formatStrategyExposure(strategy: StrategyAccounting, symbol: string): string {
    return `${this.formatAsset(this.strategyExposure(strategy), symbol)} (${strategy.address})`
  }

  private strategyValuationMethod(strategy: StrategyAccounting): string {
    if (strategy.profile.type === 'open-term loan manager') return 'loan manager assetsUnderManagement(); principalOut plus accrued interest less impairments'
    if (strategy.profile.type === 'fixed-term loan manager') return 'loan manager assetsUnderManagement(); scheduled loan accounting'
    if (strategy.profile.protocolOrCounterparty === 'Aave') return 'strategy contract assetsUnderManagement(); underlying protocol withdrawal value'
    if (strategy.profile.protocolOrCounterparty === 'Sky') return 'strategy contract assetsUnderManagement(); underlying protocol withdrawal value'
    return 'strategy contract assetsUnderManagement()'
  }

  private strategyMarkSource(strategy: StrategyAccounting): string {
    if (strategy.profile.protocolOrCounterparty === 'Maple borrowers') return 'Maple loan manager contract accounting'
    if (strategy.profile.protocolOrCounterparty === 'Aave') return 'Aave strategy contract accounting'
    if (strategy.profile.protocolOrCounterparty === 'Sky') return 'Sky strategy contract accounting'
    return 'strategy contract accounting'
  }

  private formatStrategyConcentration(strategies: StrategyAccounting[], symbol: string): string {
    if (strategies.length === 0) return `0 ${symbol}`

    return strategies
      .slice()
      .sort((left, right) => this.strategyExposure(right) - this.strategyExposure(left))
      .map((strategy) => this.formatStrategyExposure(strategy, symbol))
      .join('; ')
  }

  private formatHhi<T>(items: T[], total: number, valueOf: (item: T) => number): string {
    if (!total) return '0.0000'

    const hhi = items.reduce((sum, item) => {
      const share = valueOf(item) / total
      return sum + share ** 2
    }, 0)

    return hhi.toFixed(4)
  }

  private maxSharePriceDrawdown(history: { sharePrice: number }[]): { maxDrawdown: number; peakSharePrice: number; troughSharePrice: number } {
    let runningPeak = history[0]?.sharePrice ?? 0
    let peakSharePrice = runningPeak
    let troughSharePrice = runningPeak
    let maxDrawdown = 0

    for (const point of history) {
      if (point.sharePrice >= runningPeak) {
        runningPeak = point.sharePrice
        continue
      }

      const drawdown = this.ratio(runningPeak - point.sharePrice, runningPeak)
      if (drawdown > maxDrawdown) {
        maxDrawdown = drawdown
        peakSharePrice = runningPeak
        troughSharePrice = point.sharePrice
      }
    }

    return { maxDrawdown, peakSharePrice, troughSharePrice }
  }

  private sharePriceDeclines(history: { sharePrice: number }[]): { count: number; largestDecline: number } {
    let count = 0
    let largestDecline = 0

    for (let index = 1; index < history.length; index += 1) {
      const previous = history[index - 1].sharePrice
      const current = history[index].sharePrice
      const decline = this.ratio(previous - current, previous)
      if (decline <= 0) continue

      count += 1
      largestDecline = Math.max(largestDecline, decline)
    }

    return { count, largestDecline }
  }

  private formatSharePriceRecovery(currentSharePrice: number, allTimeHigh: number, maxDrawdown: number): string {
    if (maxDrawdown === 0) return 'No sampled share price drawdown'

    return `${formatPercent(this.ratio(currentSharePrice, allTimeHigh))} of sampled all-time high`
  }

  private formatRatio(value: number, divisor: number): string {
    return this.ratio(value, divisor).toFixed(2)
  }

  private topBy<T>(items: T[], valueOf: (item: T) => number): T | undefined {
    if (!items.length) return undefined
    return [...items].sort((left, right) => valueOf(right) - valueOf(left))[0]
  }

  private ratio(value: number, divisor: number): number {
    if (!divisor) return 0
    return value / divisor
  }

  private topExposure<T>(items: T[], count: number, valueOf: (item: T) => number): number {
    return items
      .slice()
      .sort((left, right) => valueOf(right) - valueOf(left))
      .slice(0, count)
      .reduce((sum, item) => sum + valueOf(item), 0)
  }

  private isPastDue(loan: OpenTermLoan, now: number): boolean {
    return loan.nextPaymentDue > 0 && loan.nextPaymentDue < now
  }

  private weightedAverageDaysPastDue(loans: OpenTermLoan[], now: number): number {
    return this.weightedAverage(
      loans,
      (loan) => Math.max((now - loan.nextPaymentDue) / SECONDS_PER_DAY, 0),
      (loan) => loan.principalOwed,
    )
  }

  private weightedAverageDaysUntil(loans: OpenTermLoan[], now: number): number {
    return this.weightedAverage(loans, (loan) => {
      const timestamp = loan.nextPaymentDue
      if (!timestamp) return 0
      return Math.max((timestamp - now) / SECONDS_PER_DAY, 0)
    }, (loan) => loan.principalOwed)
  }

  private maximumDaysUntil<T>(items: T[], now: number, timestampOf: (item: T) => number): number {
    return Math.max(...items.map((item) => {
      const timestamp = timestampOf(item)
      if (!timestamp) return 0
      return Math.max((timestamp - now) / SECONDS_PER_DAY, 0)
    }), 0)
  }

  private calledLoanLiquidityDueWithinDays(loans: OpenTermLoan[], now: number, days: number): number {
    const cutoff = now + days * SECONDS_PER_DAY

    return this.sumBy(
      loans.filter((loan) => loan.nextPaymentDue > 0 && loan.nextPaymentDue <= cutoff),
      (loan) => loan.principalOwed,
    )
  }

  private formatMaterialStrategyWithdrawalDelay(strategies: StrategyAccounting[]): string {
    return strategies
      .filter((strategy) => this.strategyExposure(strategy) >= MIN_MATERIAL_STRATEGY_EXPOSURE)
      .map((strategy) => `${strategy.profile.name}: ${strategy.profile.liquidityTerms}`)
      .join('; ') || 'No material active strategies'
  }

  private weightedAverage<T>(items: T[], valueOf: (item: T) => number, weightOf: (item: T) => number): number {
    const weight = this.sumBy(items, weightOf)
    if (!weight) return 0

    return items.reduce((sum, item) => sum + valueOf(item) * weightOf(item), 0) / weight
  }

  private formatDays(value: number): string {
    return `${this.formatNumber(value)} days`
  }

  private sumBy<T>(items: T[], valueOf: (item: T) => number): number {
    return items.reduce((sum, item) => sum + valueOf(item), 0)
  }

  private borrowerExposures(loans: OpenTermLoan[]): { borrower: string; exposure: number }[] {
    const exposuresByBorrower = new Map<string, number>()

    for (const loan of loans) {
      exposuresByBorrower.set(loan.borrower, (exposuresByBorrower.get(loan.borrower) ?? 0) + loan.principalOwed)
    }

    return [...exposuresByBorrower.entries()]
      .map(([borrower, exposure]) => ({ borrower, exposure }))
      .sort((left, right) => right.exposure - left.exposure)
  }

  private formatBorrowerExposure(borrower: { borrower: string; exposure: number }, symbol: string): string {
    return `${this.formatAsset(borrower.exposure, symbol)} (${borrower.borrower})`
  }

  private strategyExposure(strategy: StrategyAccounting): number {
    return Math.max(strategy.assetsUnderManagement, strategy.principalOut)
  }
}
