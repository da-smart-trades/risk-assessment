// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Contract, formatUnits, isAddress, type JsonRpcProvider } from 'ethers'
import { GraphQLClient } from '../../../lib/graphql'
import { toBigInt } from '../../../lib/numbers'
import { currentUnixTimestampSeconds, SECONDS_PER_DAY } from '../../../lib/time'
import type { Chain } from '../../../lib/web3/chains'
import { Web3Provider } from '../../../lib/web3/provider'
import {
  POOL_INDEX_DATA_QUERY,
  POOL_OPEN_TERM_LOANS_QUERY,
  POOL_POSITIONS_QUERY,
  POOL_QUEUE_QUERY,
  POOL_STRATEGIES_QUERY,
  SYRUP_POOLS_QUERY,
} from './queries'
import {
  ERC20_ABI,
  POOL_ABI,
  POOL_MANAGER_ABI,
  STRATEGY_ABI,
  type Erc20Contract,
  type PoolContract,
  type PoolManagerContract,
  type StrategyContract,
} from './contracts'
import {
  AAVE_STRATEGY_PROFILE,
  BITCOIN_STRATEGY_PROFILE,
  SKY_STRATEGY_PROFILE,
  STRATEGY_FALLBACK_PROFILE,
} from './strategies'
import { resolveMapleApiUrl } from './networks'
import type {
  LpPosition,
  MaplePool,
  OpenTermLoan,
  PoolIndexData,
  PoolIndexDataResponse,
  PoolOpenTermLoansResponse,
  PoolPositionsResponse,
  PoolQueueResponse,
  PoolStrategiesResponse,
  PoolSummary,
  StrategyAccounting,
  StrategyProfile,
  SyrupPoolsResponse,
  WithdrawalManagerQueue,
} from './types'

const TOP_LP_POSITION_LIMIT = 5
const SHARE_PRICE_SAMPLE_LIMIT = 24
const SHARE_PRICE_SAMPLE_INTERVAL_DAYS = 30
const ETHEREUM_AVERAGE_BLOCK_TIME_SECONDS = 12

type StrategyMetadataKind = 'loanManager' | 'aave' | 'sky' | 'bitcoin'

export class MapleQuerier {
  private readonly graphQLClient: GraphQLClient
  private readonly web3Provider: Web3Provider

  constructor(private readonly chain: Chain) {
    this.graphQLClient = new GraphQLClient(resolveMapleApiUrl(chain))
    this.web3Provider = new Web3Provider()
  }

  async findPool(address: string): Promise<MaplePool | undefined> {
    if (!isAddress(address)) throw new Error(`Invalid Maple pool address "${address}".`)
    const summary = await this.findPoolSummary(address)
    return this.buildPool(address, summary)
  }

  private async findPoolSummary(address: string): Promise<PoolSummary | undefined> {
    const summaries = await this.getPoolSummaries()
    return summaries.find((pool) => this.normalizeAddress(pool.id) === this.normalizeAddress(address))
  }

  private async getPoolSummaries(): Promise<PoolSummary[]> {
    const response = await this.graphQLClient.request<SyrupPoolsResponse>(SYRUP_POOLS_QUERY)
    return response.poolV2S
  }

  private async getWithdrawalQueue(poolId: string): Promise<WithdrawalManagerQueue | undefined> {
    const response = await this.graphQLClient.request<PoolQueueResponse>(POOL_QUEUE_QUERY, { poolId: this.normalizeAddress(poolId) })
    return response.poolV2?.withdrawalManagerQueue
  }

  private async getPoolIndexData(poolId: string, summary: PoolSummary | undefined): Promise<PoolIndexData> {
    if (summary) {
      return {
        delegate: summary.delegate,
        transaction: summary.transaction,
        realizedLosses: summary.realizedLosses,
        previousUnrealizedLosses: summary.previousUnrealizedLosses,
      }
    }

    const response = await this.graphQLClient.request<PoolIndexDataResponse>(POOL_INDEX_DATA_QUERY, { poolId: this.normalizeAddress(poolId) })
    if (!response.poolV2) throw new Error(`Maple pool ${poolId} was not found in the Maple API.`)

    return response.poolV2
  }

  private async getTopLpPositions(poolId: string, assetDecimals: number, poolDecimals: number): Promise<{ lpPositionCount: number; topLpPositions: LpPosition[] }> {
    const response = await this.graphQLClient.request<PoolPositionsResponse>(POOL_POSITIONS_QUERY, {
      poolId: this.normalizeAddress(poolId),
      limit: TOP_LP_POSITION_LIMIT,
    })
    const positions = response.poolV2?.positions ?? []

    return {
      lpPositionCount: Number(response.poolV2?.numPositions ?? 0),
      topLpPositions: positions.map((position) => ({
        account: position.account.id,
        availableBalance: this.tokenAmount(toBigInt(position.availableBalance), assetDecimals),
        availableShares: this.tokenAmount(toBigInt(position.availableShares), poolDecimals),
      })),
    }
  }

  private async getStrategyMetadata(poolId: string): Promise<Map<string, StrategyMetadataKind>> {
    const response = await this.graphQLClient.request<PoolStrategiesResponse>(POOL_STRATEGIES_QUERY, { poolId: this.normalizeAddress(poolId) })
    const metadata = response.poolV2
    const ids = new Map<string, StrategyMetadataKind>()
    if (metadata?.loanManager) ids.set(this.normalizeAddress(metadata.loanManager.id), 'loanManager')
    if (metadata?.skyStrategy) ids.set(this.normalizeAddress(metadata.skyStrategy.id), 'sky')
    for (const strategy of metadata?.aaveStrategies ?? []) ids.set(this.normalizeAddress(strategy.id), 'aave')
    for (const strategy of metadata?.bitcoinStrategies ?? []) ids.set(this.normalizeAddress(strategy.id), 'bitcoin')

    return ids
  }

  private async getOpenTermLoans(poolId: string, assetDecimals: number): Promise<{ openTermLoanCount: number; openTermLoans: OpenTermLoan[] }> {
    const response = await this.graphQLClient.request<PoolOpenTermLoansResponse>(POOL_OPEN_TERM_LOANS_QUERY, { poolId: this.normalizeAddress(poolId) })
    const loans = response.poolV2?.openTermLoans ?? []

    return {
      openTermLoanCount: Number(response.poolV2?.numOpenTermLoans ?? 0),
      openTermLoans: loans.map((loan) => ({
        id: loan.id,
        borrower: loan.borrower.id,
        principalOwed: this.tokenAmount(toBigInt(loan.principalOwed), assetDecimals),
        paymentIntervalDays: Number(loan.paymentIntervalDays),
        nextPaymentDue: Number(loan.nextPaymentDue),
        isCalled: loan.isCalled,
        isImpaired: loan.isImpaired,
      })),
    }
  }

  private async buildPool(poolId: string, summary: PoolSummary | undefined): Promise<MaplePool> {
    const { provider } = await this.web3Provider.connect(this.chain)
    const normalizedPoolId = this.normalizeAddress(poolId)
    const pool = new Contract(normalizedPoolId, POOL_ABI, provider) as unknown as PoolContract
    const [
      name,
      symbol,
      poolDecimalsRaw,
      assetAddress,
      managerAddress,
      totalAssetsRaw,
      totalSupplyRaw,
      unrealizedLossesRaw,
    ] = await Promise.all([
      pool.name(),
      pool.symbol(),
      pool.decimals(),
      pool.asset(),
      pool.manager(),
      pool.totalAssets(),
      pool.totalSupply(),
      pool.unrealizedLosses(),
    ])
    const asset = new Contract(assetAddress, ERC20_ABI, provider) as unknown as Erc20Contract
    const [assetSymbol, assetDecimalsRaw, availableLiquidityRaw] = await Promise.all([
      asset.symbol(),
      asset.decimals(),
      asset.balanceOf(normalizedPoolId),
    ])
    const poolDecimals = Number(poolDecimalsRaw)
    const assetDecimals = Number(assetDecimalsRaw)
    const oneShare = 10n ** BigInt(poolDecimals)
    const [sharePriceRaw, exitSharePriceRaw] = await Promise.all([
      totalSupplyRaw > 0n ? pool.convertToAssets(oneShare) : 0n,
      totalSupplyRaw > 0n ? pool.convertToExitAssets(oneShare) : 0n,
    ])
    const poolManager = new Contract(managerAddress, POOL_MANAGER_ABI, provider) as unknown as PoolManagerContract
    const [indexData, strategyMetadata, liquidityCapRaw, hasSufficientCover, poolDelegateCoverAddress, lpPositions, openTermLoanData] = await Promise.all([
      this.getPoolIndexData(normalizedPoolId, summary),
      this.getStrategyMetadata(normalizedPoolId),
      poolManager.liquidityCap(),
      poolManager.hasSufficientCover(),
      poolManager.poolDelegateCover(),
      this.getTopLpPositions(normalizedPoolId, assetDecimals, poolDecimals),
      this.getOpenTermLoans(normalizedPoolId, assetDecimals),
    ])
    const strategies = await this.getStrategies(poolManager, provider, assetDecimals, this.normalizeAddress(assetAddress), strategyMetadata)
    const poolDelegateCoverSharesRaw = await pool.balanceOf(poolDelegateCoverAddress)
    const poolDelegateCoverRaw = poolDelegateCoverSharesRaw > 0n ? await pool.convertToExitAssets(poolDelegateCoverSharesRaw) : 0n
    const queue = summary?.withdrawalManagerQueue ?? await this.getWithdrawalQueue(normalizedPoolId)
    const withdrawalQueueSharesRaw = queue ? toBigInt(queue.totalShares) : 0n
    const pendingRedemptionsRaw = withdrawalQueueSharesRaw > 0n ? await pool.convertToExitAssets(withdrawalQueueSharesRaw) : 0n
    const totalAssets = this.tokenAmount(totalAssetsRaw, assetDecimals)
    const availableLiquidity = this.tokenAmount(availableLiquidityRaw, assetDecimals)
    const sharePrice = this.tokenAmount(sharePriceRaw, assetDecimals)
    const sharePriceHistory = await this.getSharePriceHistory(
      pool,
      provider,
      oneShare,
      assetDecimals,
      sharePrice,
      Number(indexData.transaction.timestamp),
    )
    const liquidityCap = this.tokenAmount(liquidityCapRaw, assetDecimals)
    const principalOutstanding = this.sumBy(strategies, (strategy) => strategy.principalOut)
    const accruedInterest = this.sumBy(strategies, (strategy) => strategy.accruedInterest)
    const strategyAssetsUnderManagement = this.sumBy(strategies, (strategy) => strategy.assetsUnderManagement)

    return {
      id: normalizedPoolId,
      name,
      symbol,
      managerAddress: this.normalizeAddress(managerAddress),
      poolDelegateAddress: this.normalizeAddress(indexData.delegate.id),
      assetAddress: this.normalizeAddress(assetAddress),
      assetSymbol,
      assetDecimals,
      poolDecimals,
      totalAssets,
      totalSupply: this.tokenAmount(totalSupplyRaw, poolDecimals),
      totalSupplyValue: totalAssets,
      sharePrice,
      sharePriceHistory,
      exitSharePrice: this.tokenAmount(exitSharePriceRaw, assetDecimals),
      liquidityCap,
      liquidityCapacityRemaining: Math.max(liquidityCap - totalAssets, 0),
      availableLiquidity,
      assetsUnderManagement: strategyAssetsUnderManagement,
      principalOutstanding,
      accruedInterest,
      realizedLosses: this.tokenAmount(toBigInt(indexData.realizedLosses), assetDecimals),
      unrealizedLosses: Math.max(this.tokenAmount(unrealizedLossesRaw, assetDecimals), this.sumBy(strategies, (strategy) => strategy.unrealizedLosses)),
      previousUnrealizedLosses: this.tokenAmount(toBigInt(indexData.previousUnrealizedLosses), assetDecimals),
      poolDelegateCoverAddress: this.normalizeAddress(poolDelegateCoverAddress),
      poolDelegateCover: this.tokenAmount(poolDelegateCoverRaw, assetDecimals),
      hasSufficientCover,
      withdrawalQueueShares: this.tokenAmount(withdrawalQueueSharesRaw, poolDecimals),
      pendingRedemptions: this.tokenAmount(pendingRedemptionsRaw, assetDecimals),
      nextWithdrawalRequest: queue?.nextRequest ? `${queue.nextRequest.id}; ${queue.nextRequest.status}` : 'none',
      lpPositionCount: lpPositions.lpPositionCount,
      topLpPositions: lpPositions.topLpPositions,
      strategies,
      openTermLoanCount: openTermLoanData.openTermLoanCount,
      openTermLoans: openTermLoanData.openTermLoans,
    }
  }

  private async getStrategies(
    poolManager: PoolManagerContract,
    provider: JsonRpcProvider,
    assetDecimals: number,
    assetAddress: string,
    strategyMetadata: Map<string, StrategyMetadataKind>,
  ): Promise<StrategyAccounting[]> {
    const strategyCount = Number(await poolManager.strategyListLength())
    const strategies: StrategyAccounting[] = []

    for (let index = 0; index < strategyCount; index += 1) {
      const address = await poolManager.strategyList(index)
      const strategy = new Contract(address, STRATEGY_ABI, provider) as unknown as StrategyContract
      const [strategyAssetAddress, assetsUnderManagement, principalOut, unrealizedLosses, accruedInterest] = await Promise.all([
        this.optionalAddress(() => strategy.fundsAsset(), assetAddress),
        this.optionalTokenAmount(() => strategy.assetsUnderManagement(), assetDecimals),
        this.optionalTokenAmount(() => strategy.principalOut(), assetDecimals),
        this.optionalTokenAmount(() => strategy.unrealizedLosses(), assetDecimals),
        this.optionalTokenAmount(() => strategy.accruedInterest(), assetDecimals),
      ])
      const normalizedAddress = this.normalizeAddress(address)
      const profile = await this.strategyProfile(strategy, normalizedAddress, strategyMetadata)

      strategies.push({
        address: normalizedAddress,
        assetAddress: this.normalizeAddress(strategyAssetAddress),
        assetsUnderManagement,
        principalOut,
        unrealizedLosses,
        accruedInterest,
        profile,
      })
    }

    return strategies
  }

  private async getSharePriceHistory(
    pool: PoolContract,
    provider: JsonRpcProvider,
    oneShare: bigint,
    assetDecimals: number,
    currentSharePrice: number,
    startTimestamp: number,
  ) {
    const latestBlock = await provider.getBlock('latest')
    if (!latestBlock) return [{ timestamp: currentUnixTimestampSeconds(), sharePrice: currentSharePrice }]

    const timestamps = this.sharePriceSampleTimestamps(startTimestamp, latestBlock.timestamp)
    const points = await Promise.all(timestamps.map(async (timestamp) => {
      const blockTag = this.estimateBlockAtTimestamp(latestBlock.number, latestBlock.timestamp, timestamp)
      try {
        const sharePrice = this.tokenAmount(await pool.convertToAssets(oneShare, { blockTag }), assetDecimals)
        return { timestamp, sharePrice }
      } catch {
        return undefined
      }
    }))

    return [
      ...points.filter((point): point is { timestamp: number; sharePrice: number } => Boolean(point)),
      { timestamp: latestBlock.timestamp, sharePrice: currentSharePrice },
    ].sort((left, right) => left.timestamp - right.timestamp)
  }

  private sharePriceSampleTimestamps(startTimestamp: number, endTimestamp: number): number[] {
    if (!startTimestamp || startTimestamp >= endTimestamp) return []

    const sampleIntervalSeconds = SHARE_PRICE_SAMPLE_INTERVAL_DAYS * SECONDS_PER_DAY
    const sampleCount = Math.min(SHARE_PRICE_SAMPLE_LIMIT, Math.max(Math.floor((endTimestamp - startTimestamp) / sampleIntervalSeconds), 1))
    const interval = (endTimestamp - startTimestamp) / sampleCount

    return Array.from({ length: sampleCount }, (_, index) => Math.floor(startTimestamp + index * interval))
  }

  private estimateBlockAtTimestamp(latestBlockNumber: number, latestTimestamp: number, timestamp: number): number {
    return Math.max(latestBlockNumber - Math.round((latestTimestamp - timestamp) / ETHEREUM_AVERAGE_BLOCK_TIME_SECONDS), 0)
  }

  private async strategyProfile(
    strategy: StrategyContract,
    address: string,
    metadata: Map<string, StrategyMetadataKind>,
  ): Promise<StrategyProfile> {
    const metadataKind = metadata.get(address)
    if (metadataKind === 'aave') return AAVE_STRATEGY_PROFILE
    if (metadataKind === 'sky') return SKY_STRATEGY_PROFILE
    if (metadataKind === 'bitcoin') return BITCOIN_STRATEGY_PROFILE
    if (await this.canRead(() => strategy.principalOut())) return await this.loanManagerProfile(strategy)

    return STRATEGY_FALLBACK_PROFILE
  }

  private async loanManagerProfile(strategy: StrategyContract): Promise<StrategyProfile> {
    const hasPaymentCounter = await this.canRead(() => strategy.paymentCounter())

    return {
      name: hasPaymentCounter ? 'Maple fixed-term loan manager' : 'Maple open-term loan manager',
      type: hasPaymentCounter ? 'fixed-term loan manager' : 'open-term loan manager',
      protocolOrCounterparty: 'Maple borrowers',
      liquidityTerms: hasPaymentCounter ? 'scheduled loan payments' : 'open-term loan repayments and call mechanics',
      maturityProfile: hasPaymentCounter ? 'fixed-term loan schedule' : 'open-term credit exposure',
      collateralization: 'borrower loan terms',
      riskCategory: 'direct credit exposure',
      externalDependencies: 'Maple loan manager, borrower repayment, pool delegate underwriting',
    }
  }

  private async optionalTokenAmount(read: () => Promise<bigint>, decimals: number): Promise<number> {
    try {
      return this.tokenAmount(await read(), decimals)
    } catch {
      return 0
    }
  }

  private async optionalAddress(read: () => Promise<string>, fallback: string): Promise<string> {
    try {
      return await read()
    } catch {
      return fallback
    }
  }

  private async canRead(read: () => Promise<unknown>): Promise<boolean> {
    try {
      await read()
      return true
    } catch {
      return false
    }
  }

  private tokenAmount(value: bigint, decimals: number): number {
    return Number(formatUnits(value, decimals))
  }

  private sumBy<T>(items: T[], valueOf: (item: T) => number): number {
    return items.reduce((sum, item) => sum + valueOf(item), 0)
  }

  private normalizeAddress(value: string): string {
    return value.toLowerCase()
  }
}
