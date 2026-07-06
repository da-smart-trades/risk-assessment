// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

export type ReserveTokenAddresses = {
  spTokenAddress: string
  variableDebtTokenAddress: string
}

export const SPARK_RESERVE_TOKEN_ADDRESSES: Record<string, ReserveTokenAddresses> = {
  CBBTC: {
    spTokenAddress: '0xb3973D459df38ae57797811F2A1fd061DA1BC123',
    variableDebtTokenAddress: '0x661fE667D2103eb52d3632a3eB2cAbd123F27938',
  },
  DAI: {
    spTokenAddress: '0x4DEDf26112B3Ec8eC46e7E31EA5e123490B05B8B',
    variableDebtTokenAddress: '0xf705d2B7e92B3F38e6ae7afaDAA2fEE110fE5914',
  },
  EZETH: {
    spTokenAddress: '0xB131cD463d83782d4DE33e00e35EF034F0869bA1',
    variableDebtTokenAddress: '0xB0B14Dd477E6159B4F3F210cF45F0954F57c0FAb',
  },
  GNO: {
    spTokenAddress: '0x7b481aCC9fDADDc9af2cBEA1Ff2342CB1733E50F',
    variableDebtTokenAddress: '0x57a2957651DA467fCD4104D749f2F3684784c25a',
  },
  LBTC: {
    spTokenAddress: '0xa9d4EcEBd48C282a70CfD3c469d6C8F178a5738E',
    variableDebtTokenAddress: '0x096bdDFEE63F44A97cC6D2945539Ee7C8f94637D',
  },
  PYUSD: {
    spTokenAddress: '0x779224df1c756b4EDD899854F32a53E8c2B2ce5d',
    variableDebtTokenAddress: '0x3357D2DB7763D6Cd3a99f0763EbF87e0096D95f9',
  },
  RETH: {
    spTokenAddress: '0x9985dF20D7e9103ECBCeb16a84956434B6f06ae8',
    variableDebtTokenAddress: '0xBa2C8F2eA5B56690bFb8b709438F049e5Dd76B96',
  },
  RSETH: {
    spTokenAddress: '0x856f1Ea78361140834FDCd0dB0b08079e4A45062',
    variableDebtTokenAddress: '0xc528F0C91CFAE4fd86A68F6Dfd4d7284707Bec68',
  },
  SDAI: {
    spTokenAddress: '0x78f897F0fE2d3B5690EbAe7f19862DEacedF10a7',
    variableDebtTokenAddress: '0xaBc57081C04D921388240393ec4088Aa47c6832B',
  },
  SUSDS: {
    spTokenAddress: '0x6715bc100A183cc65502F05845b589c1919ca3d3',
    variableDebtTokenAddress: '0x4e89b83f426fED3f2EF7Bb2d7eb5b53e288e1A13',
  },
  TBTC: {
    spTokenAddress: '0xce6Ca9cDce00a2b0c0d1dAC93894f4Bd2c960567',
    variableDebtTokenAddress: '0x764591dC9ba21c1B92049331b80b6E2a2acF8B17',
  },
  USDC: {
    spTokenAddress: '0x377C3bd93f2a2984E1E7bE6A5C22c525eD4A4815',
    variableDebtTokenAddress: '0x7B70D04099CB9cfb1Db7B6820baDAfB4C5C70A67',
  },
  USDS: {
    spTokenAddress: '0xC02aB1A5eaA8d1B114EF786D9bde108cD4364359',
    variableDebtTokenAddress: '0x8c147debea24Fb98ade8dDa4bf142992928b449e',
  },
  USDT: {
    spTokenAddress: '0xe7dF13b8e3d6740fe17CBE928C7334243d86c92f',
    variableDebtTokenAddress: '0x529b6158d1D2992E3129F7C69E81a7c677dc3B12',
  },
  WBTC: {
    spTokenAddress: '0x4197ba364AE6698015AE5c1468f54087602715b2',
    variableDebtTokenAddress: '0xf6fEe3A8aC8040C3d6d81d9A4a168516Ec9B51D2',
  },
  WEETH: {
    spTokenAddress: '0x3CFd5C0D4acAA8Faee335842e4f31159fc76B008',
    variableDebtTokenAddress: '0xc2bD6d2fEe70A0A73a33795BdbeE0368AeF5c766',
  },
  WETH: {
    spTokenAddress: '0x59cD1C87501baa753d0B5B5Ab5D8416A45cD71DB',
    variableDebtTokenAddress: '0x2e7576042566f8D6990e07A1B61Ad1efd86Ae70d',
  },
  WSTETH: {
    spTokenAddress: '0x12B54025C112Aa61fAce2CDB7118740875A566E9',
    variableDebtTokenAddress: '0xd5c3E3B566a42A6110513Ac7670C1a86D76E13E6',
  },
}
