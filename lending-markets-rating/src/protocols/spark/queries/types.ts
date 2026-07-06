// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

export type Market = {
  symbol: string
  underlyingAddress: string
  underlyingName: string
  spTokenAddress: string
  variableDebtTokenAddress: string
  totalSupplyUsd: number
  totalBorrowUsd: number
  availableLiquidityUsd: number
  supplyApy: number
  borrowApy: number
  utilizationRate: number
  optimalUsageRate: number
  baseVariableBorrowRate: number
  variableRateSlope1: number
  variableRateSlope2: number
  underlyingPrice: number
  usageAsCollateralEnabled: boolean
  borrowingEnabled: boolean
  isFrozen: boolean
  borrowingIsolationMode: boolean
  collateralIsolationMode: boolean
  ltv: number
  liquidationThreshold: number
  liquidationBonus: number
  reserveFactor: number
  supplyCap: number
  supplyCapUsd: number
  borrowCap: number
  borrowCapUsd: number
  tags: string[]
}

export type ResolvedReserve = {
  reserve: Market
  reserves: Market[]
  reserveId: string
}

export type SupplierPosition = {
  supplyUsd: number
}

export type BorrowerPosition = {
  borrowUsd: number
}

export type ReserveHistory = {
  totalSupplyUsd: number
  totalBorrowUsd: number
  availableLiquidityUsd: number
  utilization: number
}

export type DebtCollateralization = {
  symbol: string
  amountUsd: number
}

export type QueryParams = Record<string, string | number | undefined>

export type MarketResponse = {
  symbol: string
  underlying_address: string
  underlying_name: string
  total_supply_usd: number
  total_borrow_usd: number
  tvl_usd: number
  supply_apy: string
  borrow_variable_apy: string
  utilization_rate: number
  underlying_price: string
  usage_as_collateral_enabled: boolean
  borrowing_enabled: boolean
  is_frozen: boolean
  borrowing_isolation_mode: boolean
  collateral_isolation_mode: boolean
  ltv: string
  liquidation_threshold: string
  liquidation_bonus: string
  reserve_factor: string
  tags: string[]
}

export type MarketParams = {
  underlying_symbol: string
  underlying_address: string
  borrow_cap: string
  supply_cap: string
  optimal_usage_ratio: string
  base_variable_borrow_rate: string
  variable_rate_slope_1: string
  variable_rate_slope_2: string
}

export type MarketParamsResponse = {
  results: MarketParams[]
}

export type MarketPosition = {
  supply_usd: number
  borrow_usd: number
}

export type MarketPositionsResponse = {
  results: MarketPosition[]
}

export type HistoricalMarket = {
  total_supply_usd: string
  total_borrow_usd: string
  available_liquidity: string
  utilization: string
}

export type DebtCollateralizationResponse = {
  key: string
  amount: string
}
