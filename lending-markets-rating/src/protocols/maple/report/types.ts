// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import type { Report } from '../../../lib/reports/types'

export interface MaplePoolReport extends Report<MapleAnchorsEvidence, MapleModifiersEvidence> {
  poolName: string
  poolSymbol: string
  asset: string
}

export interface MapleAnchorsEvidence {
  poolAssetCoverage: PoolAssetCoverageEvidence
  redemptionLiquidityCoverage: RedemptionLiquidityCoverageEvidence
  loanPerformanceImpairment: LoanPerformanceImpairmentEvidence
}

export interface MapleModifiersEvidence {
  recoveryRobustness: RecoveryRobustnessEvidence
  loanBookDiversification: LoanBookDiversificationEvidence
  managerTrackRecord: ManagerTrackRecordEvidence
}

export interface PoolAssetCoverageEvidence {
  totalAssets: string
  totalSupply: string
  currentSharePrice: string
  allTimeHighSharePrice: string
  sharePriceMaxDrawdown: string
  historicalSharePriceDeclines: string
  sharePriceRecoveryEvidence: string
  principalOutstanding: string
  assetsUnderManagement: string
  availableLiquidity: string
  accruedInterest: string
  unrealizedLosses: string
  poolDelegateCover: string
  hasSufficientCover: string
  liquidityCap: string
  accountingAssetCoverageRatio: string
  lossCoverageRatio: string
  assetValuationMethod: string
  assetMarkSource: string
  assetFairValueVsBookValue: string
  strategyValuation: StrategyValuationEvidence[]
}

export interface StrategyValuationEvidence {
  strategyAddress: string
  strategyType: string
  exposure: string
  valuationMethod: string
  markSource: string
}

export interface RedemptionLiquidityCoverageEvidence {
  availableLiquidity: string
  totalAssets: string
  availableLiquidityRatio: string
  openTermLoanCount: string
  activeOpenTermLoanCount: string
  calledLoanCount: string
  calledPrincipal: string
  next7DaysExpectedLiquidity: string
  next30DaysExpectedLiquidity: string
  next90DaysExpectedLiquidity: string
  weightedAverageNextPaymentDays: string
  maximumNextPaymentDays: string
  weightedAveragePaymentIntervalDays: string
  maximumPaymentIntervalDays: string
  liquidityRecallPeriod: string
  strategyWithdrawalDelay: string
  largestLp30DayExitCoverage: string
  largestLp90DayExitCoverage: string
  withdrawalQueue: string
  pendingRedemptions: string
  lockedShares: string
  liquidityCap: string
  remainingCapacityToLiquidityCap: string
  lpPositionCount: string
  largestLpBalance: string
  topFiveLpConcentration: string
  liquidityCoverageRatio: string
  queueCoverageRatio: string
  largestLpBalanceVsAvailableLiquidity: string
  largestLpExitCoverage: string
}

export interface LoanPerformanceImpairmentEvidence {
  strategyCount: string
  strategiesWithPrincipalOutstanding: string
  activeLoanCount: string
  calledLoanCount: string
  impairedLoanCount: string
  lateLoanCount: string
  totalPrincipalOutstanding: string
  assetsUnderManagement: string
  accruedInterest: string
  calledPrincipal: string
  pastDuePrincipal: string
  latePrincipal: string
  impairedPrincipal: string
  nonPerformingLoanRatio: string
  weightedAverageDaysPastDue: string
  impairmentCoverage: string
}

export interface RecoveryRobustnessEvidence {
  poolDelegateCoverContract: string
  poolDelegateCoverBalance: string
  hasSufficientCover: string
  currentUnrealizedLosses: string
  currentRealizedLosses: string
  unrealizedLossesToPrincipal: string
  lossCoverageRatio: string
  recoveryProcessTransparency: string
}

export interface LoanBookDiversificationEvidence {
  strategyCount: string
  activeStrategyCount: string
  largestStrategyExposure: string
  largestStrategyExposureRatio: string
  topFiveStrategyExposure: string
  topFiveStrategyExposureRatio: string
  strategyHHI: string
  strategyConcentration: string
  strategyExposures: StrategyExposureEvidence[]
  activeLoanCount: string
  borrowerCount: string
  largestBorrowerExposure: string
  largestBorrowerExposureRatio: string
  topFiveBorrowerExposure: string
  topFiveBorrowerExposureRatio: string
  topTenBorrowerExposure: string
  topTenBorrowerExposureRatio: string
  borrowerHHI: string
}

export interface StrategyExposureEvidence {
  strategyAddress: string
  strategyName: string
  strategyType: string
  protocolOrCounterparty: string
  exposure: string
  exposureRatio: string
  asset: string
  liquidityTerms: string
  maturityProfile: string
  collateralization: string
  riskCategory: string
  externalDependencies: string
}

export interface ManagerTrackRecordEvidence {
  poolDelegateAddress: string
  poolManagerAddress: string
  activePrincipalManaged: string
  activeAssetsManaged: string
  realizedLosses: string
  unrealizedLosses: string
  previousUnrealizedLosses: string
  realizedLossesToActivePrincipal: string
}
