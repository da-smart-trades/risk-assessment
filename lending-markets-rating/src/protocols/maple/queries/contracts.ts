// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

export const POOL_ABI = [
  'function name() view returns (string)',
  'function symbol() view returns (string)',
  'function decimals() view returns (uint8)',
  'function asset() view returns (address)',
  'function manager() view returns (address)',
  'function totalAssets() view returns (uint256)',
  'function totalSupply() view returns (uint256)',
  'function unrealizedLosses() view returns (uint256)',
  'function convertToAssets(uint256 shares) view returns (uint256)',
  'function convertToExitAssets(uint256 shares) view returns (uint256)',
  'function balanceOf(address owner) view returns (uint256)',
]

export const ERC20_ABI = [
  'function symbol() view returns (string)',
  'function decimals() view returns (uint8)',
  'function balanceOf(address owner) view returns (uint256)',
]

export const POOL_MANAGER_ABI = [
  'function liquidityCap() view returns (uint256)',
  'function hasSufficientCover() view returns (bool)',
  'function poolDelegateCover() view returns (address)',
  'function strategyListLength() view returns (uint256)',
  'function strategyList(uint256 index) view returns (address)',
]

export const STRATEGY_ABI = [
  'function fundsAsset() view returns (address)',
  'function assetsUnderManagement() view returns (uint256)',
  'function principalOut() view returns (uint256)',
  'function unrealizedLosses() view returns (uint256)',
  'function accruedInterest() view returns (uint256)',
  'function paymentCounter() view returns (uint24)',
]

export interface BlockTagOverride {
  blockTag: number
}

export interface PoolContract {
  name(): Promise<string>
  symbol(): Promise<string>
  decimals(): Promise<bigint>
  asset(): Promise<string>
  manager(): Promise<string>
  totalAssets(): Promise<bigint>
  totalSupply(): Promise<bigint>
  unrealizedLosses(): Promise<bigint>
  convertToAssets(shares: bigint, overrides?: BlockTagOverride): Promise<bigint>
  convertToExitAssets(shares: bigint): Promise<bigint>
  balanceOf(owner: string): Promise<bigint>
}

export interface Erc20Contract {
  symbol(): Promise<string>
  decimals(): Promise<bigint>
  balanceOf(owner: string): Promise<bigint>
}

export interface PoolManagerContract {
  liquidityCap(): Promise<bigint>
  hasSufficientCover(): Promise<boolean>
  poolDelegateCover(): Promise<string>
  strategyListLength(): Promise<bigint>
  strategyList(index: number): Promise<string>
}

export interface StrategyContract {
  fundsAsset(): Promise<string>
  assetsUnderManagement(): Promise<bigint>
  principalOut(): Promise<bigint>
  unrealizedLosses(): Promise<bigint>
  accruedInterest(): Promise<bigint>
  paymentCounter(): Promise<bigint>
}
