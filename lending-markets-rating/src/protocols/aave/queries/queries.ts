// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

export const AAVE_MARKETS_QUERY = `
  query($chainId: ChainId!) {
    markets(request: { chainIds: [$chainId] }) {
      address
      reserves {
        underlyingToken {
          address
        }
        aToken {
          address
        }
        vToken {
          address
        }
      }
    }
  }
`

export const AAVE_MARKET_QUERY = `
  query($chainId: ChainId!, $marketAddress: EvmAddress!) {
    market(request: { chainId: $chainId, address: $marketAddress }) {
      reserves {
        underlyingToken {
          address
          symbol
          decimals
        }
        aToken {
          symbol
        }
        size {
          usd
        }
        usdExchangeRate
        supplyInfo {
          apy {
            value
          }
          maxLTV {
            value
          }
          liquidationThreshold {
            value
          }
          liquidationBonus {
            value
          }
          canBeCollateral
          supplyCap {
            usd
          }
        }
        borrowInfo {
          apy {
            value
          }
          total {
            usd
          }
          reserveFactor {
            value
          }
          availableLiquidity {
            usd
          }
          utilizationRate {
            value
          }
          baseVariableBorrowRate {
            value
          }
          variableRateSlope1 {
            value
          }
          variableRateSlope2 {
            value
          }
          optimalUsageRate {
            value
          }
          borrowingState
          borrowCap {
            usd
          }
        }
        isFrozen
      }
    }
  }
`

export const AAVE_RESERVE_ID_BY_UNDERLYING_QUERY = `
  query($underlyingAsset: Bytes!) {
    reserves(where: { underlyingAsset: $underlyingAsset }, first: 1) {
      id
    }
  }
`

export const AAVE_SUPPLIERS_QUERY = `
  query($reserveId: String!, $first: Int!) {
    userReserves(
      first: $first
      orderBy: currentATokenBalance
      orderDirection: desc
      where: { reserve: $reserveId, currentATokenBalance_gt: "0" }
    ) {
      currentATokenBalance
    }
  }
`

export const AAVE_BORROWERS_QUERY = `
  query($reserveId: String!, $first: Int!) {
    userReserves(
      first: $first
      orderBy: currentTotalDebt
      orderDirection: desc
      where: { reserve: $reserveId, currentTotalDebt_gt: "0" }
    ) {
      currentTotalDebt
    }
  }
`

export const AAVE_LIQUIDATION_CALLS_QUERY = `
  query($reserveId: String!, $timestampGte: Int!, $first: Int!) {
    liquidationCalls(
      first: $first
      orderBy: timestamp
      orderDirection: desc
      where: { principalReserve: $reserveId, timestamp_gte: $timestampGte }
    ) {
      principalAmount
      collateralAmount
      collateralAssetPriceUSD
      borrowAssetPriceUSD
      collateralReserve {
        id
        symbol
        decimals
      }
      principalReserve {
        id
        symbol
        decimals
      }
    }
  }
`

export function buildAaveDailyHistoryQuery(dayTimestamps: number[]): string {
  const dailyFields = dayTimestamps.map((timestamp, index) => `
    day${index}: reserveParamsHistoryItems(
      first: 1
      orderBy: timestamp
      orderDirection: desc
      where: {
        reserve: $reserveId
        timestamp_lte: ${Math.trunc(timestamp)}
      }
    ) {
      timestamp
      availableLiquidity
      totalCurrentVariableDebt
      totalPrincipalStableDebt
    }
  `).join('\n')

  return `
    query($reserveId: String!) {
      ${dailyFields}
    }
  `
}
