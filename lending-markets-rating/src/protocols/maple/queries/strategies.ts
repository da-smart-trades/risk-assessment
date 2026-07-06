// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import type { StrategyProfile } from './types'

export const AAVE_STRATEGY_PROFILE: StrategyProfile = {
  name: 'Aave strategy',
  type: 'DeFi lending strategy',
  protocolOrCounterparty: 'Aave',
  liquidityTerms: 'Aave withdrawal liquidity',
  maturityProfile: 'open-ended protocol position',
  collateralization: 'Aave market collateralization model',
  riskCategory: 'DeFi protocol and liquidity exposure',
  externalDependencies: 'Aave protocol, Aave market liquidity, strategy contract implementation',
}

export const SKY_STRATEGY_PROFILE: StrategyProfile = {
  name: 'Sky strategy',
  type: 'DeFi yield strategy',
  protocolOrCounterparty: 'Sky',
  liquidityTerms: 'Sky strategy withdrawal path',
  maturityProfile: 'open-ended protocol position',
  collateralization: 'Sky strategy mechanics',
  riskCategory: 'DeFi protocol exposure',
  externalDependencies: 'Sky protocol and strategy contract implementation',
}

export const BITCOIN_STRATEGY_PROFILE: StrategyProfile = {
  name: 'Bitcoin strategy',
  type: 'Bitcoin-linked strategy',
  protocolOrCounterparty: 'Bitcoin strategy counterparty',
  liquidityTerms: 'strategy-specific withdrawal path',
  maturityProfile: 'strategy-specific',
  collateralization: 'strategy-specific',
  riskCategory: 'Bitcoin-linked strategy exposure',
  externalDependencies: 'Bitcoin strategy infrastructure and strategy contract implementation',
}

export const STRATEGY_FALLBACK_PROFILE: StrategyProfile = {
  name: 'Maple strategy',
  type: 'strategy contract',
  protocolOrCounterparty: 'strategy contract',
  liquidityTerms: 'strategy-specific withdrawal path',
  maturityProfile: 'strategy-specific',
  collateralization: 'strategy-specific',
  riskCategory: 'strategy contract exposure',
  externalDependencies: 'strategy contract implementation',
}
