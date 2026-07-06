// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import type { Chain } from '../../../lib/web3/chains'

const MAPLE_API_URLS: Record<number, string> = {
  1: 'https://api.maple.finance/v2/graphql',
}

export const MAPLE_CHAINS: Chain[] = [
  { id: 1, network: 'Ethereum' },
]

export function resolveMapleApiUrl(chain: Chain): string {
  const url = MAPLE_API_URLS[chain.id]
  if (!url) throw new Error(`Unsupported Maple chain id "${chain.id}".`)
  return url
}
