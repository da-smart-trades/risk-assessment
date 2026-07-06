// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

export interface PoolAsset {
  symbol: string
  decimals: number
}

export interface SyrupRouter {
  id: string
}

export interface WithdrawalRequest {
  id: string
  shares: string
  status: string
}

export interface WithdrawalManagerQueue {
  id: string
  totalShares: string
  nextRequest: WithdrawalRequest | null
}

export interface PoolDelegate {
  id: string
}

export interface PoolTransaction {
  timestamp: string
}

export interface PoolSummary {
  id: string
  name: string
  delegate: PoolDelegate
  transaction: PoolTransaction
  realizedLosses: string
  previousUnrealizedLosses: string
  asset: PoolAsset
  syrupRouter: SyrupRouter
  withdrawalManagerQueue: WithdrawalManagerQueue
}

export interface SyrupPoolsResponse {
  poolV2S: PoolSummary[]
}

export interface PoolQueue {
  withdrawalManagerQueue: WithdrawalManagerQueue
}

export interface PoolQueueResponse {
  poolV2: PoolQueue | null
}

export interface PoolIndexData {
  delegate: PoolDelegate
  transaction: PoolTransaction
  realizedLosses: string
  previousUnrealizedLosses: string
}

export interface PoolIndexDataResponse {
  poolV2: PoolIndexData | null
}

export interface PoolPositionAccount {
  id: string
}

export interface PoolPosition {
  id: string
  availableBalance: string
  availableShares: string
  account: PoolPositionAccount
}

export interface PoolPositions {
  numPositions: string
  positions: PoolPosition[]
}

export interface PoolPositionsResponse {
  poolV2: PoolPositions | null
}

export interface LpPosition {
  account: string
  availableBalance: number
  availableShares: number
}

export interface StrategyMetadataItem {
  id: string
}

export interface PoolStrategyMetadata {
  loanManager: StrategyMetadataItem | null
  aaveStrategies: StrategyMetadataItem[]
  skyStrategy: StrategyMetadataItem | null
  bitcoinStrategies: StrategyMetadataItem[]
}

export interface PoolStrategiesResponse {
  poolV2: PoolStrategyMetadata | null
}

export interface OpenTermLoanBorrower {
  id: string
}

export interface OpenTermLoanRecord {
  id: string
  principalOwed: string
  paymentIntervalDays: string
  nextPaymentDue: string
  isCalled: boolean
  isImpaired: boolean
  borrower: OpenTermLoanBorrower
}

export interface PoolOpenTermLoans {
  numOpenTermLoans: string
  openTermLoans: OpenTermLoanRecord[]
}

export interface PoolOpenTermLoansResponse {
  poolV2: PoolOpenTermLoans | null
}

export interface OpenTermLoan {
  id: string
  borrower: string
  principalOwed: number
  paymentIntervalDays: number
  nextPaymentDue: number
  isCalled: boolean
  isImpaired: boolean
}

export interface StrategyProfile {
  name: string
  type: string
  protocolOrCounterparty: string
  liquidityTerms: string
  maturityProfile: string
  collateralization: string
  riskCategory: string
  externalDependencies: string
}

export interface StrategyAccounting {
  address: string
  assetAddress: string
  assetsUnderManagement: number
  principalOut: number
  unrealizedLosses: number
  accruedInterest: number
  profile: StrategyProfile
}

export interface SharePricePoint {
  timestamp: number
  sharePrice: number
}

export interface MaplePool {
  id: string
  name: string
  symbol: string
  managerAddress: string
  poolDelegateAddress: string
  assetAddress: string
  assetSymbol: string
  assetDecimals: number
  poolDecimals: number
  totalAssets: number
  totalSupply: number
  totalSupplyValue: number
  sharePrice: number
  sharePriceHistory: SharePricePoint[]
  exitSharePrice: number
  liquidityCap: number
  liquidityCapacityRemaining: number
  availableLiquidity: number
  assetsUnderManagement: number
  principalOutstanding: number
  accruedInterest: number
  realizedLosses: number
  unrealizedLosses: number
  previousUnrealizedLosses: number
  poolDelegateCoverAddress: string
  poolDelegateCover: number
  hasSufficientCover: boolean
  withdrawalQueueShares: number
  pendingRedemptions: number
  nextWithdrawalRequest: string
  lpPositionCount: number
  topLpPositions: LpPosition[]
  strategies: StrategyAccounting[]
  openTermLoanCount: number
  openTermLoans: OpenTermLoan[]
}
