// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

export type Token = {
  address: string
  symbol: string
  decimals: number
}

export type SymbolToken = {
  symbol: string
}

export type TokenAddress = {
  address: string
}

export type PercentValue = {
  value: number
}

export type TokenAmount = {
  usd: number
}

export type SupplyInfo = {
  apy: PercentValue
  maxLTV: PercentValue
  liquidationThreshold: PercentValue
  liquidationBonus: PercentValue
  canBeCollateral: boolean
  supplyCap: TokenAmount
}

export type BorrowInfo = {
  apy: PercentValue
  total: TokenAmount
  reserveFactor: PercentValue
  availableLiquidity: TokenAmount
  utilizationRate: PercentValue
  baseVariableBorrowRate: PercentValue
  variableRateSlope1: PercentValue
  variableRateSlope2: PercentValue
  optimalUsageRate: PercentValue
  borrowingState: string
  borrowCap: TokenAmount
}

export type Reserve = {
  underlyingToken: Token
  aToken: SymbolToken
  size: TokenAmount
  usdExchangeRate: number
  supplyInfo: SupplyInfo
  borrowInfo: BorrowInfo | null
  isFrozen: boolean
}

export type Market = {
  reserves: Reserve[]
}

export type ReserveTokenAddresses = {
  underlyingToken: TokenAddress
  aToken: TokenAddress
  vToken: TokenAddress
}

export type MarketReserveTokens = {
  address: string
  reserves: ReserveTokenAddresses[]
}

export type MarketsQueryResponse = {
  markets: MarketReserveTokens[]
}

export type MarketQueryResponse = {
  market: Market | null
}

export type ReserveId = {
  id: string
}

export type SupplierPosition = {
  currentATokenBalance: number
}

export type BorrowerPosition = {
  currentTotalDebt: number
}

export type LiquidationReserve = {
  id: string
  symbol: string
  decimals: number
}

export type LiquidationCallRecord = {
  principalAmount: string
  collateralAmount: string
  collateralAssetPriceUSD: string
  borrowAssetPriceUSD: string
  collateralReserve: LiquidationReserve
  principalReserve: LiquidationReserve
}

export type LiquidationCallsQueryResponse = {
  liquidationCalls: LiquidationCallRecord[]
}

export type CollateralLiquidationStats = {
  collateralSymbol: string
  eventCount: number
  debtRepaidUsd: number
  collateralSeizedUsd: number
}

export type LiquidationHistory = {
  eventCount: number
  debtRepaidUsd: number
  collateralSeizedUsd: number
  collateralStats: CollateralLiquidationStats[]
}

export type ReserveHistory = {
  timestamp: number
  availableLiquidity: number
  totalCurrentVariableDebt: number
  totalPrincipalStableDebt: number
}

export type ReserveQueryResponse = {
  reserves: ReserveId[]
}

export type SupplierPositionsQueryResponse = {
  userReserves: SupplierPosition[]
}

export type BorrowerPositionsQueryResponse = {
  userReserves: BorrowerPosition[]
}

export type DailyHistoryQueryResponse = Record<string, ReserveHistory[]>
