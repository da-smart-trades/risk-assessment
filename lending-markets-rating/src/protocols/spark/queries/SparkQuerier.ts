// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import type { Chain } from '../../../lib/web3/chains'
import { SPARK_RESERVE_TOKEN_ADDRESSES } from './addresses'
import { resolveSparkApiBaseUrl } from './networks'
import type {
  BorrowerPosition,
  DebtCollateralization,
  DebtCollateralizationResponse,
  HistoricalMarket,
  Market,
  MarketParams,
  MarketParamsResponse,
  MarketPositionsResponse,
  MarketResponse,
  QueryParams,
  ReserveHistory,
  ResolvedReserve,
  SupplierPosition,
} from './types'

const HISTORY_WINDOW_DAYS = 90
const EMPTY_TOKEN_ADDRESSES = { spTokenAddress: '', variableDebtTokenAddress: '' }

class SparkApiClient {
  constructor(private readonly baseUrl: string) {}

  async get<T>(path: string, query: QueryParams = {}): Promise<T> {
    const url = new URL(`${this.baseUrl}/${path.replace(/^\/+/, '')}`)
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) url.searchParams.set(key, String(value))
    }

    const response = await fetch(url)
    const body = await response.json().catch(() => null) as T | null

    if (!response.ok) throw new Error(`Spark API returned HTTP ${response.status} for ${url.pathname}`)
    if (!body) throw new Error(`Spark API returned an invalid JSON response for ${url.pathname}`)

    return body
  }
}

export class SparkQuerier {
  private readonly apiClient: SparkApiClient

  constructor(private readonly chain: Chain) {
    this.apiClient = new SparkApiClient(resolveSparkApiBaseUrl(chain))
  }

  async findReserve(address: string): Promise<ResolvedReserve | undefined> {
    const reserves = await this.getReserves()
    const reserve = reserves.find((item) => (
      this.matchesAddress(item.underlyingAddress, address)
      || this.matchesAddress(item.spTokenAddress, address)
      || this.matchesAddress(item.variableDebtTokenAddress, address)
      || item.symbol.toLowerCase() === address.toLowerCase()
    ))
    if (!reserve) return undefined

    return { reserve, reserves, reserveId: reserve.symbol }
  }

  async getSuppliers(reserveId: string, limit: number): Promise<SupplierPosition[]> {
    const response = await this.apiClient.get<MarketPositionsResponse>(`markets/${reserveId}/wallets/`, {
      p: 1,
      p_size: limit,
      order: '-supply',
    })

    return response.results.map((position) => ({
      supplyUsd: position.supply_usd,
    }))
  }

  async getBorrowers(reserveId: string, limit: number): Promise<BorrowerPosition[]> {
    const response = await this.apiClient.get<MarketPositionsResponse>(`markets/${reserveId}/wallets/`, {
      p: 1,
      p_size: limit,
      order: '-borrow',
    })

    return response.results.map((position) => ({
      borrowUsd: position.borrow_usd,
    }))
  }

  async getHistoricalState(reserveId: string): Promise<ReserveHistory[]> {
    const response = await this.apiClient.get<HistoricalMarket[]>(`markets/${reserveId}/historic-details/`, {
      days_ago: HISTORY_WINDOW_DAYS,
    })

    return response.map((record) => ({
      totalSupplyUsd: Number(record.total_supply_usd),
      totalBorrowUsd: Number(record.total_borrow_usd),
      availableLiquidityUsd: Number(record.available_liquidity),
      utilization: Number(record.utilization),
    }))
  }

  async getDebtCollateralization(reserveId: string): Promise<DebtCollateralization[]> {
    const response = await this.apiClient.get<DebtCollateralizationResponse[]>(`markets/${reserveId}/debt-collateralization/`)

    return response.map((record) => ({
      symbol: record.key,
      amountUsd: Number(record.amount),
    }))
  }

  private async getReserves(): Promise<Market[]> {
    const [markets, params] = await Promise.all([
      this.apiClient.get<MarketResponse[]>('markets/'),
      this.apiClient.get<MarketParamsResponse>('markets/params/'),
    ])
    const paramsByAddress = new Map(params.results.map((param) => [this.normalizeAddress(param.underlying_address), param]))

    return markets.map((market) => this.buildReserve(market, this.requireParams(market, paramsByAddress)))
  }

  private buildReserve(market: MarketResponse, params: MarketParams): Market {
    const tokenAddresses = SPARK_RESERVE_TOKEN_ADDRESSES[market.symbol.toUpperCase()] ?? EMPTY_TOKEN_ADDRESSES
    const underlyingPrice = Number(market.underlying_price)
    const supplyCap = Number(params.supply_cap)
    const borrowCap = Number(params.borrow_cap)

    return {
      symbol: market.symbol,
      underlyingAddress: market.underlying_address,
      underlyingName: market.underlying_name,
      spTokenAddress: tokenAddresses.spTokenAddress,
      variableDebtTokenAddress: tokenAddresses.variableDebtTokenAddress,
      totalSupplyUsd: market.total_supply_usd,
      totalBorrowUsd: market.total_borrow_usd,
      availableLiquidityUsd: market.tvl_usd,
      supplyApy: Number(market.supply_apy),
      borrowApy: Number(market.borrow_variable_apy),
      utilizationRate: market.utilization_rate,
      optimalUsageRate: Number(params.optimal_usage_ratio),
      baseVariableBorrowRate: Number(params.base_variable_borrow_rate),
      variableRateSlope1: Number(params.variable_rate_slope_1),
      variableRateSlope2: Number(params.variable_rate_slope_2),
      underlyingPrice,
      usageAsCollateralEnabled: market.usage_as_collateral_enabled,
      borrowingEnabled: market.borrowing_enabled,
      isFrozen: market.is_frozen,
      borrowingIsolationMode: market.borrowing_isolation_mode,
      collateralIsolationMode: market.collateral_isolation_mode,
      ltv: Number(market.ltv),
      liquidationThreshold: Number(market.liquidation_threshold),
      liquidationBonus: Number(market.liquidation_bonus),
      reserveFactor: Number(market.reserve_factor),
      supplyCap,
      supplyCapUsd: supplyCap * underlyingPrice,
      borrowCap,
      borrowCapUsd: borrowCap * underlyingPrice,
      tags: market.tags,
    }
  }

  private requireParams(market: MarketResponse, paramsByAddress: Map<string, MarketParams>): MarketParams {
    const params = paramsByAddress.get(this.normalizeAddress(market.underlying_address))
    if (params) return params

    throw new Error(`Spark params for ${market.symbol} were not found.`)
  }

  private matchesAddress(candidate: string, expected: string): boolean {
    return Boolean(candidate) && this.normalizeAddress(candidate) === this.normalizeAddress(expected)
  }

  private normalizeAddress(value: string): string {
    return value.toLowerCase()
  }
}
