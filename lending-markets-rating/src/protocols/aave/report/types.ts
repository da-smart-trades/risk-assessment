// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import type { Report } from '../../../lib/reports/types'

export interface MarketReport extends Report<MarketAnchorsEvidence, ControlModifiersEvidence> {
  loanAsset: string
  loanFamily: string
}

export interface MarketAnchorsEvidence {
  marketSolvency: MarketSolvencyEvidence
  withdrawalLiquidity: WithdrawalLiquidityEvidence
}

export interface MarketSolvencyEvidence {
  totalSupplied: string
  totalBorrowed: string
  idleLiquidity: string
  utilizationRate: string
  supplyRate: string
  borrowRate: string
  totalReserves: string
  reserveFactor: string
  currentBadDebt: string
  historicalBadDebt: string
  underwaterAccounts: string
  reserveCoverageVsBadDebt: string
  recentLiquidationVolume: string
  recentDebtRepaid: string
  recentCollateralSeized: string
  failedLiquidations: string
  badDebtAfterLiquidations: string
  collaterals: CollateralSolvencyEvidence[]
  existenceOfCapsOrIsolationMechanisms: string
  supplyCap: string
  borrowCap: string
}

export interface WithdrawalLiquidityEvidence {
  idleLiquidity: string
  utilizationRate: string
  topFiveSupplierConcentration: string
  topTenSupplierConcentration: string
  topFiveBorrowerConcentration: string
  topTenBorrowerConcentration: string
  historicalUtilizationAverage: string
  historicalUtilizationPeak: string
  optimalUtilization: string
  baseRate: string
  slope1: string
  slope2: string
  rateResponseAtHighUtilization: string
  largestSupplierBalanceVsIdleLiquidity: string
  largestSupplierExitCoverage: string
  topFiveSupplierExitCoverage: string
  topTenSupplierExitCoverage: string
  tenPercentSupplyExitCoverage: string
  twentyFivePercentSupplyExitCoverage: string
  borrowAssetsVsIdleLiquidity: string
}

export interface ControlModifiersEvidence {
  collateralDependencyRobustness: CollateralDependencyRobustnessEvidence
  collateralAndLiquidityDiversification: CollateralAndLiquidityDiversificationEvidence
}

export interface CollateralDependencyRobustnessEvidence {
  collaterals: CollateralDependencyEvidence[]
  reserveFactor: string
  historicalUtilization: string
}

export interface CollateralAndLiquidityDiversificationEvidence {
  concentrationInTopCollateralAssets: string
  exposureToLoanAssetCorrelatedCollateral: string
  exposureToInternallyCorrelatedCollateralFamilies: string
  topFiveBorrowerConcentration: string
  topTenBorrowerConcentration: string
  topFiveSupplierConcentration: string
  topTenSupplierConcentration: string
  largestSupplierBalanceVsIdleLiquidity: string
}

export interface CollateralSolvencyEvidence {
  asset: string
  family: string
  exposure: string
  maximumLtv: string
  shareOfBorrowPool: string
  protocolLiquidity: string
  marketLiquidityForLiquidations: string
  historicalLiquidationPerformance: string
  liquidationVenueDepth: string
  liquidationThreshold: string
  liquidationIncentive: string
}

export interface CollateralDependencyEvidence {
  asset: string
  flags: string
  family: string
  maximumLtv: string
  liquidationThreshold: string
  liquidationPenalty: string
}
