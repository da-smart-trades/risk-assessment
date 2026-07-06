// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { GraphQLClient, type GraphQLVariables } from '../../../lib/graphql'
import { currentUnixTimestampSeconds, SECONDS_PER_DAY } from '../../../lib/time'
import type { Chain } from '../../../lib/web3/chains'
import {
  BORROWERS_QUERY,
  HISTORICAL_MARKET_QUERY,
  MARKET_QUERY,
  SUPPLIERS_QUERY,
} from './queries'
import type {
  Borrower,
  BorrowerPositionData,
  HistoricalMarketState,
  HistoricalMarketResponse,
  HistoricalSeriesData,
  HistoricalUtilizationPoint,
  Market,
  MarketPositionsQueryResponse,
  MarketQueryData,
  MarketQueryResponse,
  Supplier,
  SupplierPositionData,
} from './types'

const DEFAULT_API_URL = 'https://blue-api.morpho.org/graphql'
const HISTORY_WINDOW_DAYS = 90

export class MorphoQuerier {
  private readonly graphQLClient: GraphQLClient

  constructor() {
    this.graphQLClient = new GraphQLClient(DEFAULT_API_URL)
  }

  async query<TData>(query: string, variables: GraphQLVariables = {}): Promise<TData> {
    return this.graphQLClient.request<TData>(query, variables)
  }

  async getMarket(chain: Chain, marketId: string): Promise<Market> {
    const response = await this.query<MarketQueryResponse>(MARKET_QUERY, { chainId: chain.id, marketId })
    if (!response.marketByUniqueKey) throw new Error(`Market ${marketId} was not found on chain ${chain.id}`)

    return this.buildMarket(response.marketByUniqueKey)
  }

  async getBorrowers(chain: Chain, marketId: string, limit: number): Promise<Borrower[]> {
    return this.getBorrowerPositions(BORROWERS_QUERY, chain, marketId, limit)
  }

  async getSuppliers(chain: Chain, marketId: string, limit: number): Promise<Supplier[]> {
    return this.getSupplierPositions(SUPPLIERS_QUERY, chain, marketId, limit)
  }

  async getHistoricalMarket(chain: Chain, marketId: string): Promise<HistoricalMarketState> {
    const response = await this.query<HistoricalMarketResponse>(HISTORICAL_MARKET_QUERY, {
      chainId: chain.id,
      marketId,
      options: this.buildHistoricalOptions(),
    })

    return this.buildHistoricalMarket(response)
  }

  private async getBorrowerPositions(query: string, chain: Chain, marketId: string, limit: number): Promise<Borrower[]> {
    const items = await this.getPositions<BorrowerPositionData>(query, chain, marketId, limit)

    return items
      .map((position): Borrower => {
        return {
          borrowAssetsUsd: position.state.borrowAssetsUsd,
        }
      })
      .filter((position) => position.borrowAssetsUsd > 0)
  }

  private async getSupplierPositions(query: string, chain: Chain, marketId: string, limit: number): Promise<Supplier[]> {
    const items = await this.getPositions<SupplierPositionData>(query, chain, marketId, limit)

    return items
      .map((position): Supplier => {
        return {
          supplyAssetsUsd: position.state.supplyAssetsUsd,
        }
      })
      .filter((position) => position.supplyAssetsUsd > 0)
  }

  private async getPositions<TPosition>(query: string, chain: Chain, marketId: string, limit: number): Promise<TPosition[]> {
    const data = await this.query<MarketPositionsQueryResponse<TPosition>>(query, {
      chainId: chain.id,
      marketId,
      first: limit,
    })

    return data.marketPositions.items
  }

  private buildMarket(rawMarket: MarketQueryData): Market {
    return {
      lltv: rawMarket.lltv,
      collateralAsset: rawMarket.collateralAsset,
      loanAsset: rawMarket.loanAsset,
      realizedBadDebtUsd: rawMarket.realizedBadDebt.usd,
      state: rawMarket.state,
    }
  }

  private buildHistoricalMarket(response: HistoricalMarketResponse): HistoricalMarketState {
    const history = response.marketByUniqueKey.historicalState

    const pointsByTimestamp = new Map<number, HistoricalUtilizationPoint>()
    this.mergeHistoricalSeries(pointsByTimestamp, history.supplyAssetsUsd, (point, value) => {
      point.supplyAssetsUsd = value
    })
    this.mergeHistoricalSeries(pointsByTimestamp, history.borrowAssetsUsd, (point, value) => {
      point.borrowAssetsUsd = value
    })

    if (!pointsByTimestamp.size) throw new Error('Historical market data is empty.')

    const points = [...pointsByTimestamp.values()].sort((left, right) => left.timestamp - right.timestamp)
    for (const point of points) {
      point.utilization = point.supplyAssetsUsd > 0 ? point.borrowAssetsUsd / point.supplyAssetsUsd : 0
    }
    const utilizations = points.map((point) => point.utilization)

    return {
      available: true,
      averageUtilization: this.average(utilizations),
      peakUtilization: Math.max(...utilizations, 0),
    }
  }

  private mergeHistoricalSeries(
    pointsByTimestamp: Map<number, HistoricalUtilizationPoint>,
    series: HistoricalSeriesData[],
    assign: (point: HistoricalUtilizationPoint, value: number) => void,
  ): void {
    for (const item of series) {
      const point = pointsByTimestamp.get(item.x) ?? {
        timestamp: item.x,
        supplyAssetsUsd: 0,
        borrowAssetsUsd: 0,
        utilization: 0,
      }

      assign(point, item.y)
      pointsByTimestamp.set(item.x, point)
    }
  }

  private average(values: number[]): number {
    if (!values.length) return 0
    return values.reduce((sum, value) => sum + value, 0) / values.length
  }

  private buildHistoricalOptions(): { startTimestamp: number; endTimestamp: number; interval: 'DAY' } {
    const endTimestamp = currentUnixTimestampSeconds()
    const startTimestamp = endTimestamp - HISTORY_WINDOW_DAYS * SECONDS_PER_DAY

    return {
      startTimestamp,
      endTimestamp,
      interval: 'DAY',
    }
  }
}
