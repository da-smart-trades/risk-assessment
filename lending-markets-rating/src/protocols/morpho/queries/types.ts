// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import type { BigNumber } from '../../../lib/numbers'

export interface UsdValue {
  usd: number
}

export interface Asset {
  symbol: string
  tags: string[]
}

export interface CollateralAsset extends Asset {
  isListed: boolean
}

export interface MarketState {
  borrowAssetsUsd: number
  supplyAssetsUsd: number
  liquidityAssetsUsd: number
  utilization: number
  supplyApy: number
  borrowApy: number
  fee: number
}

export interface Market {
  lltv: BigNumber
  collateralAsset: CollateralAsset
  loanAsset: Asset
  realizedBadDebtUsd: number
  state: MarketState
}

export interface Borrower {
  borrowAssetsUsd: number
}

export interface Supplier {
  supplyAssetsUsd: number
}

export interface HistoricalMarketState {
  available: boolean
  averageUtilization: number
  peakUtilization: number
}

export interface HistoricalUtilizationPoint {
  timestamp: number
  supplyAssetsUsd: number
  borrowAssetsUsd: number
  utilization: number
}

export interface MarketQueryData {
  lltv: BigNumber
  collateralAsset: CollateralAsset
  loanAsset: Asset
  realizedBadDebt: UsdValue
  state: MarketState
}

export interface BorrowerPositionState {
  borrowAssetsUsd: number
}

export interface SupplierPositionState {
  supplyAssetsUsd: number
}

export interface BorrowerPositionData {
  state: BorrowerPositionState
}

export interface SupplierPositionData {
  state: SupplierPositionState
}

export interface MarketPositionsQueryResponse<TPosition> {
  marketPositions: {
    items: TPosition[]
  }
}

export type MarketQueryResponse = {
  marketByUniqueKey: MarketQueryData
}

export interface HistoricalSeriesData {
  x: number
  y: number
}

export type HistoricalMarketResponse = {
  marketByUniqueKey: {
    historicalState: {
      supplyAssetsUsd: HistoricalSeriesData[]
      borrowAssetsUsd: HistoricalSeriesData[]
    }
  }
}
