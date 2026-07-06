// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

// Chain logos
import chainARBITRUM from "@/assets/logos/chain-ARBITRUM.png"
import chainAVALANCHE_C from "@/assets/logos/chain-AVALANCHE_C.png"
import chainBASE from "@/assets/logos/chain-BASE.png"
import chainCANTON from "@/assets/logos/chain-CANTON.svg"
import chainETHEREUM from "@/assets/logos/chain-ETHEREUM.png"
import chainINK from "@/assets/logos/chain-INK.svg"
import chainOPTIMISM from "@/assets/logos/chain-OPTIMISM.svg"
import chainPOLYGON from "@/assets/logos/chain-POLYGON.png"
import chainSOLANA from "@/assets/logos/chain-SOLANA.png"
import chainUNICHAIN from "@/assets/logos/chain-UNICHAIN.svg"

// Protocol logos
import protocolAAVE_V3 from "@/assets/logos/protocol-AAVE_V3.png"
import protocolCOMPOUND_V3 from "@/assets/logos/protocol-COMPOUND_V3.png"
import protocolDRIFT_V2 from "@/assets/logos/protocol-DRIFT_V2.png"
import protocolMORPHO_V2 from "@/assets/logos/protocol-MORPHO_V2.svg"

// Token logos
import tokenAAVE from "@/assets/logos/token-AAVE.png"
import tokenAUSDC from "@/assets/logos/token-AUSDC.png"
import tokenCBBTC from "@/assets/logos/token-CBBTC.svg"
import tokenCUSDC from "@/assets/logos/token-CUSDC.png"
import tokenLINK from "@/assets/logos/token-LINK.png"
import tokenSTETH from "@/assets/logos/token-STETH.png"
import tokenUNI from "@/assets/logos/token-UNI.png"
import tokenUSDC from "@/assets/logos/token-USDC.png"
import tokenUSDE from "@/assets/logos/token-USDE.svg"
import tokenUSDT0 from "@/assets/logos/token-USDT0.png"
import tokenWETH from "@/assets/logos/token-WETH.svg"
import tokenWSTETH from "@/assets/logos/token-WSTETH.png"

export const PROTOCOL_LOGOS: Record<string, string> = {
	AAVE_V3: protocolAAVE_V3,
	MORPHO_V2: protocolMORPHO_V2,
	COMPOUND_V3: protocolCOMPOUND_V3,
	DRIFT_V2: protocolDRIFT_V2,
}

export const CHAIN_LOGOS: Record<string, string> = {
	ARBITRUM: chainARBITRUM,
	AVALANCHE_C: chainAVALANCHE_C,
	BASE: chainBASE,
	CANTON: chainCANTON,
	ETHEREUM: chainETHEREUM,
	INK: chainINK,
	OPTIMISM: chainOPTIMISM,
	POLYGON: chainPOLYGON,
	SOLANA: chainSOLANA,
	UNICHAIN: chainUNICHAIN,
}

export const TOKEN_LOGOS: Record<string, string> = {
	AAVE: tokenAAVE,
	AUSDC: tokenAUSDC,
	CBBTC: tokenCBBTC,
	CUSDC: tokenCUSDC,
	LINK: tokenLINK,
	STETH: tokenSTETH,
	UNI: tokenUNI,
	USDC: tokenUSDC,
	USDE: tokenUSDE,
	USDT0: tokenUSDT0,
	WETH: tokenWETH,
	WSTETH: tokenWSTETH,
}
