// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { GraphQLClient } from '../../../lib/graphql'
import { currentUnixTimestampSeconds, SECONDS_PER_DAY } from '../../../lib/time'
import type { Chain } from '../../../lib/web3/chains'
import { resolveAaveSubgraphUrl } from './subgraphs'
import {
  AAVE_BORROWERS_QUERY,
  AAVE_LIQUIDATION_CALLS_QUERY,
  AAVE_MARKET_QUERY,
  AAVE_MARKETS_QUERY,
  AAVE_RESERVE_ID_BY_UNDERLYING_QUERY,
  AAVE_SUPPLIERS_QUERY,
  buildAaveDailyHistoryQuery,
} from './queries'
import type {
  BorrowerPosition,
  BorrowerPositionsQueryResponse,
  CollateralLiquidationStats,
  DailyHistoryQueryResponse,
  LiquidationCallsQueryResponse,
  LiquidationHistory,
  Market,
  MarketQueryResponse,
  MarketReserveTokens,
  MarketsQueryResponse,
  Reserve,
  ReserveHistory,
  ReserveQueryResponse,
  SupplierPosition,
  SupplierPositionsQueryResponse,
} from './types'

const AAVE_API_ENDPOINT = 'https://api.v3.aave.com/graphql'
const HISTORY_WINDOW_DAYS = 90
const HISTORY_QUERY_CHUNK_SIZE = 30
const LIQUIDATION_HISTORY_WINDOW_DAYS = 90
const LIQUIDATION_HISTORY_LIMIT = 1000

export type ResolvedReserve = {
  market: Market
  reserve: Reserve
  reserveId: string
}

type MatchedReserve = {
  marketAddress: string
  underlyingAddress: string
}

export class AaveQuerier {
  private readonly apiClient: GraphQLClient
  private readonly subgraphClient: GraphQLClient

  constructor(private readonly chain: Chain) {
    this.apiClient = new GraphQLClient(AAVE_API_ENDPOINT)
    this.subgraphClient = new GraphQLClient(resolveAaveSubgraphUrl(chain))
  }

  async findReserve(address: string): Promise<ResolvedReserve | undefined> {
    const match = await this.findReserveToken(address)
    if (!match) return undefined

    const market = await this.getMarket(match.marketAddress)
    if (!market) return undefined

    const reserve = market.reserves.find((item) => this.matchesAddress(item.underlyingToken.address, match.underlyingAddress))
    if (!reserve) return undefined

    const reserveId = await this.reserveIdForUnderlying(reserve.underlyingToken.address)
    if (!reserveId) return undefined

    return { market, reserve, reserveId }
  }

  async getSuppliers(reserveId: string, limit: number): Promise<SupplierPosition[]> {
    const response = await this.subgraphClient.request<SupplierPositionsQueryResponse>(AAVE_SUPPLIERS_QUERY, {
      reserveId,
      first: limit,
    })

    for (const position of response.userReserves) position.currentATokenBalance = Number(position.currentATokenBalance)

    return response.userReserves
  }

  async getBorrowers(reserveId: string, limit: number): Promise<BorrowerPosition[]> {
    const response = await this.subgraphClient.request<BorrowerPositionsQueryResponse>(AAVE_BORROWERS_QUERY, {
      reserveId,
      first: limit,
    })

    for (const position of response.userReserves) position.currentTotalDebt = Number(position.currentTotalDebt)

    return response.userReserves
  }

  async getHistoricalState(reserveId: string): Promise<ReserveHistory[]> {
    const { startTimestamp, endTimestamp } = this.buildHistoricalRange()
    const dayTimestamps = this.buildDailyHistoryTimestamps(startTimestamp, endTimestamp)
    const dailyRecords: ReserveHistory[] = []

    for (let offset = 0; offset < dayTimestamps.length; offset += HISTORY_QUERY_CHUNK_SIZE) {
      const chunk = dayTimestamps.slice(offset, offset + HISTORY_QUERY_CHUNK_SIZE)
      const response = await this.subgraphClient.request<DailyHistoryQueryResponse>(
        buildAaveDailyHistoryQuery(chunk),
        { reserveId },
      )

      for (let index = 0; index < chunk.length; index += 1) {
        const record = response[`day${index}`]?.[0]
        if (!record) continue

        this.normalizeReserveHistory(record)
        dailyRecords.push({ ...record, timestamp: chunk[index] })
      }
    }

    return dailyRecords
  }

  async getLiquidationHistory(reserveId: string): Promise<LiquidationHistory> {
    const timestampGte = currentUnixTimestampSeconds() - LIQUIDATION_HISTORY_WINDOW_DAYS * SECONDS_PER_DAY
    const response = await this.subgraphClient.request<LiquidationCallsQueryResponse>(AAVE_LIQUIDATION_CALLS_QUERY, {
      reserveId,
      timestampGte,
      first: LIQUIDATION_HISTORY_LIMIT,
    })

    const collateralStats = new Map<string, CollateralLiquidationStats>()
    let debtRepaidUsd = 0
    let collateralSeizedUsd = 0

    for (const liquidation of response.liquidationCalls) {
      liquidation.collateralReserve.decimals = Number(liquidation.collateralReserve.decimals)
      liquidation.principalReserve.decimals = Number(liquidation.principalReserve.decimals)

      const liquidationDebtRepaidUsd = this.tokenAmountUsd(
        liquidation.principalAmount,
        liquidation.principalReserve.decimals,
        liquidation.borrowAssetPriceUSD,
      )
      const liquidationCollateralSeizedUsd = this.tokenAmountUsd(
        liquidation.collateralAmount,
        liquidation.collateralReserve.decimals,
        liquidation.collateralAssetPriceUSD,
      )
      const collateralSymbol = liquidation.collateralReserve.symbol
      const existingStats = collateralStats.get(collateralSymbol) ?? {
        collateralSymbol,
        eventCount: 0,
        debtRepaidUsd: 0,
        collateralSeizedUsd: 0,
      }

      existingStats.eventCount += 1
      existingStats.debtRepaidUsd += liquidationDebtRepaidUsd
      existingStats.collateralSeizedUsd += liquidationCollateralSeizedUsd
      collateralStats.set(collateralSymbol, existingStats)
      debtRepaidUsd += liquidationDebtRepaidUsd
      collateralSeizedUsd += liquidationCollateralSeizedUsd
    }

    return {
      eventCount: response.liquidationCalls.length,
      debtRepaidUsd,
      collateralSeizedUsd,
      collateralStats: [...collateralStats.values()].sort((left, right) => right.debtRepaidUsd - left.debtRepaidUsd),
    }
  }

  private buildDailyHistoryTimestamps(startTimestamp: number, endTimestamp: number): number[] {
    const timestamps: number[] = []
    for (
      let dayTimestamp = startTimestamp + SECONDS_PER_DAY;
      dayTimestamp <= endTimestamp;
      dayTimestamp += SECONDS_PER_DAY
    ) {
      timestamps.push(dayTimestamp)
    }

    return timestamps
  }

  private buildHistoricalRange(): { startTimestamp: number; endTimestamp: number } {
    const endTimestamp = currentUnixTimestampSeconds()
    const startTimestamp = endTimestamp - HISTORY_WINDOW_DAYS * SECONDS_PER_DAY

    return { startTimestamp, endTimestamp }
  }

  private async getMarkets(): Promise<MarketReserveTokens[]> {
    const response = await this.apiClient.request<MarketsQueryResponse>(AAVE_MARKETS_QUERY, { chainId: this.chain.id })
    return response.markets
  }

  private async getMarket(marketAddress: string): Promise<Market | null> {
    const response = await this.apiClient.request<MarketQueryResponse>(AAVE_MARKET_QUERY, {
      chainId: this.chain.id,
      marketAddress,
    })
    if (!response.market) return null

    for (const reserve of response.market.reserves) this.normalizeReserve(reserve)

    return response.market
  }

  private async findReserveToken(address: string): Promise<MatchedReserve | undefined> {
    const normalized = this.normalizeAddress(address)
    const markets = await this.getMarkets()

    for (const market of markets) {
      const reserves = market.reserves

      const reserveByToken = reserves.find((reserve) => this.matchesAddress(reserve.underlyingToken.address, normalized))
        ?? reserves.find((reserve) => this.matchesAddress(reserve.aToken.address, normalized))
        ?? reserves.find((reserve) => this.matchesAddress(reserve.vToken.address, normalized))

      if (reserveByToken) {
        return {
          marketAddress: market.address,
          underlyingAddress: reserveByToken.underlyingToken.address,
        }
      }
    }

    return undefined
  }

  private async reserveIdForUnderlying(underlyingAddress: string): Promise<string | undefined> {
    const response = await this.subgraphClient.request<ReserveQueryResponse>(AAVE_RESERVE_ID_BY_UNDERLYING_QUERY, {
      underlyingAsset: this.normalizeAddress(underlyingAddress),
    })

    return response.reserves[0]?.id
  }

  private matchesAddress(candidate: string, expected: string): boolean {
    return this.normalizeAddress(candidate) === this.normalizeAddress(expected)
  }

  private normalizeReserve(reserve: Reserve): void {
    reserve.underlyingToken.decimals = Number(reserve.underlyingToken.decimals)
    reserve.size.usd = Number(reserve.size.usd)
    reserve.usdExchangeRate = Number(reserve.usdExchangeRate)
    reserve.supplyInfo.apy.value = Number(reserve.supplyInfo.apy.value)
    reserve.supplyInfo.maxLTV.value = Number(reserve.supplyInfo.maxLTV.value)
    reserve.supplyInfo.liquidationThreshold.value = Number(reserve.supplyInfo.liquidationThreshold.value)
    reserve.supplyInfo.liquidationBonus.value = Number(reserve.supplyInfo.liquidationBonus.value)
    reserve.supplyInfo.supplyCap.usd = Number(reserve.supplyInfo.supplyCap.usd)

    if (reserve.borrowInfo) {
      reserve.borrowInfo.apy.value = Number(reserve.borrowInfo.apy.value)
      reserve.borrowInfo.total.usd = Number(reserve.borrowInfo.total.usd)
      reserve.borrowInfo.reserveFactor.value = Number(reserve.borrowInfo.reserveFactor.value)
      reserve.borrowInfo.availableLiquidity.usd = Number(reserve.borrowInfo.availableLiquidity.usd)
      reserve.borrowInfo.utilizationRate.value = Number(reserve.borrowInfo.utilizationRate.value)
      reserve.borrowInfo.baseVariableBorrowRate.value = Number(reserve.borrowInfo.baseVariableBorrowRate.value)
      reserve.borrowInfo.variableRateSlope1.value = Number(reserve.borrowInfo.variableRateSlope1.value)
      reserve.borrowInfo.variableRateSlope2.value = Number(reserve.borrowInfo.variableRateSlope2.value)
      reserve.borrowInfo.optimalUsageRate.value = Number(reserve.borrowInfo.optimalUsageRate.value)
      reserve.borrowInfo.borrowCap.usd = Number(reserve.borrowInfo.borrowCap.usd)
    }
  }

  private normalizeReserveHistory(record: ReserveHistory): void {
    record.timestamp = Number(record.timestamp)
    record.availableLiquidity = Number(record.availableLiquidity)
    record.totalCurrentVariableDebt = Number(record.totalCurrentVariableDebt)
    record.totalPrincipalStableDebt = Number(record.totalPrincipalStableDebt)
  }

  private tokenAmountUsd(rawAmount: string, decimals: number, usdPrice: string): number {
    return (Number(rawAmount) / 10 ** decimals) * Number(usdPrice)
  }

  private normalizeAddress(value: string): string {
    return value.toLowerCase()
  }
}
