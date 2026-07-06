// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

export const SYRUP_POOLS_QUERY = `
  query GetMaplePools {
    poolV2S(where: { syrupRouter_not: null }) {
      id
      name
      delegate {
        id
      }
      transaction {
        timestamp
      }
      realizedLosses
      previousUnrealizedLosses
      asset {
        symbol
        decimals
      }
      syrupRouter {
        id
      }
      withdrawalManagerQueue {
        id
        totalShares
        nextRequest {
          id
          shares
          status
        }
      }
    }
  }
`

export const POOL_INDEX_DATA_QUERY = `
  query GetPoolV2IndexData($poolId: ID!) {
    poolV2(id: $poolId) {
      delegate {
        id
      }
      transaction {
        timestamp
      }
      realizedLosses
      previousUnrealizedLosses
    }
  }
`

export const POOL_QUEUE_QUERY = `
  query GetPoolV2Queue($poolId: ID!) {
    poolV2(id: $poolId) {
      withdrawalManagerQueue {
        id
        totalShares
        nextRequest {
          id
          shares
          status
        }
      }
    }
  }
`

export const POOL_POSITIONS_QUERY = `
  query GetPoolV2Positions($poolId: ID!, $limit: Int!) {
    poolV2(id: $poolId) {
      numPositions
      positions(first: $limit, orderBy: shares, orderDirection: desc) {
        id
        availableBalance
        availableShares
        account {
          id
        }
      }
    }
  }
`

export const POOL_STRATEGIES_QUERY = `
  query GetPoolV2Strategies($poolId: ID!) {
    poolV2(id: $poolId) {
      loanManager {
        id
      }
      aaveStrategies {
        id
      }
      skyStrategy {
        id
      }
      bitcoinStrategies {
        id
      }
    }
  }
`

export const POOL_OPEN_TERM_LOANS_QUERY = `
  query GetPoolV2OpenTermLoans($poolId: ID!) {
    poolV2(id: $poolId) {
      numOpenTermLoans
      openTermLoans(first: 1000, orderBy: principalOwed, orderDirection: desc) {
        id
        principalOwed
        paymentIntervalDays
        nextPaymentDue
        isCalled
        isImpaired
        borrower {
          id
        }
      }
    }
  }
`
