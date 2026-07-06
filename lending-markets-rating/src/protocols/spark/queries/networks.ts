// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import type { Chain } from '../../../lib/web3/chains'

const SPARK_API_URLS: Record<number, string> = {
  1: 'https://spark-api.blockanalitica.com/v1/ethereum',
}

export const SPARK_CHAINS: Chain[] = [
  { id: 1, network: 'Ethereum' },
]

export function resolveSparkApiBaseUrl(chain: Chain): string {
  const url = SPARK_API_URLS[chain.id]
  if (url) return url
  throw new Error(`Unsupported Spark chain id "${chain.id}".`)
}
