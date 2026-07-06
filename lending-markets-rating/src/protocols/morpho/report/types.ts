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
  collateralAsset: string
  collateralFamily: string
  collateralExposure: string
  collateralMaximumLtv: string
  collateralShareOfDebt: string
  collateralLiquidityProfile: string
  collateralLiquidationThreshold: string
  collateralLiquidationIncentive: string
  historicalBadDebtInTheMarket: string
  existenceOfCapsOrIsolationMechanisms: string
  evidenceOfSolvencyDuringPriorStressEvents: string
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
  largestSupplierBalanceVsIdleLiquidity: string
  borrowAssetsVsIdleLiquidity: string
}

export interface ControlModifiersEvidence {
  collateralDependencyRobustness: CollateralDependencyRobustnessEvidence
  collateralAndLiquidityDiversification: CollateralAndLiquidityDiversificationEvidence
}

export interface CollateralDependencyRobustnessEvidence {
  collateralAsset: string
  collateralFlags: string
  collateralFamily: string
  collateralMaximumLtv: string
  collateralLiquidationThreshold: string
  collateralLiquidationPenalty: string
  reserveFactor: string
  historicalBadDebt: string
  historicalUtilization: string
}

export interface CollateralAndLiquidityDiversificationEvidence {
  concentrationInTopCollateralAssets: string
  exposureToCorrelatedCollateral: string
  topFiveBorrowerConcentration: string
  topTenBorrowerConcentration: string
  topFiveSupplierConcentration: string
  topTenSupplierConcentration: string
  largestSupplierBalanceVsIdleLiquidity: string
  historicalBadDebt: string
}
