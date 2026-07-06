# cbBTC Token — Risk Framework Definitions

## Designed Economic Claim

cbBTC is a Coinbase-issued wrapped Bitcoin token. Its designed economic claim is that each cbBTC represents a 1:1 redeemable claim on native BTC held by Coinbase, with token supply, custody, minting, burning, and redemption processes preserving that equivalence across supported networks.

A default occurs when outstanding cbBTC is not fully backed by BTC, supply cannot be reliably reconciled to backing, eligible holders cannot redeem through the designed Coinbase process, or token-control powers compromise the enforceability of holder claims.

## Anchors

Anchors are the fundamental risk drivers. A default occurs when one or more anchors fail.

### Reserve backing sufficiency

The BTC reserve controlled by Coinbase must remain sufficient to cover all outstanding cbBTC liabilities at the promised 1:1 basis.

### Issuance and burn accounting integrity

cbBTC must only be minted when corresponding BTC is reserved and must be burned when redeemed, so token supply remains reconciled to the BTC backing pool.

### Redemption enforceability

Eligible holders must be able to unwrap cbBTC into native BTC through Coinbase under the designed redemption process without material impairment.

### On-chain token-control integrity

The token ledger and administrative control plane must not allow unauthorized minting, burning, freezing, pausing, or upgrades that break holder claims.

## Control Modifiers

Control modifiers adjust the base probability of default upward or downward based on systemic factors.

### Custody and issuer concentration

The degree to which reserve custody, minting, burning, administrative roles, and redemption execution depend on Coinbase-controlled systems.

### Access and jurisdictional constraints

The extent to which redemption and use depend on Coinbase account access, KYC, jurisdictional eligibility, compliance screening, and legal terms.

### Deployment and integration surface

The number of supported networks and DeFi integrations that expand the operational, accounting, and token-control surface.

### Admin role scope

The breadth of privileged token actions, including upgrade, pause, blacklist, minter configuration, and role reassignment powers.

## Assurance Multipliers

Assurance multipliers reduce (or occasionally increase) the probability of default based on the quality of security and operational controls.

### Reserve transparency and proof of reserves

The quality of reserve visibility, liability reconciliation, and independent reporting available to verify the 1:1 backing claim.

### Contract security assurance

The extent to which the token contracts are audited, reused, tested, and based on mature code.

### Testing and repository transparency

The availability of public source code, test practices, and development controls relevant to the token implementation.

### Coinbase custody and operational maturity

The maturity of Coinbase custody, key management, reconciliation, and operational controls used to support cbBTC.

### Incidents history

The historical record of failures affecting cbBTC backing, redemption, token controls, custody, or supply integrity.
