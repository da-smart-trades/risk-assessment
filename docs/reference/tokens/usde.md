# USDe Token — Risk Framework Definitions

## Designed Economic Claim

Ethena is a stablecoin protocol built on Ethereum. USDe is backed by crypto assets (Bitcoin, Ethereum, Solana), yield from staked ETH through Liquid Staking Tokens (LSTs), and funding rate earnings from short futures positions on centralized exchanges.

Its designed economic claim is that each USDe represents roughly one dollar of value backed by crypto collateral whose price risk is hedged using short futures, so that the combined position behaves like a stable dollar.

## Anchors

Anchors are the fundamental risk drivers. A default occurs when one or more anchors fail.

### Hedge integrity

The derivatives hedge effectively neutralizes the price exposure of the backing collateral. Short futures positions can be opened, maintained, and rebalanced so that movements in the price of the underlying crypto assets do not materially affect the dollar value backing USDe.

### Collateral custody integrity

The crypto assets backing USDe are securely held and cannot be expropriated, misappropriated, or lost due to custody failures. This includes both on-chain collateral and assets held on centralized exchanges or custodial infrastructure used for hedging.

### Exchange and counterparty integrity

The centralized exchanges and counterparties used to maintain hedge positions remain solvent and accessible. The protocol is able to maintain margin, manage positions, and avoid forced liquidations or loss of assets due to exchange failure.

### Operational risk management

The operational processes responsible for managing collateral, maintaining hedges, and adjusting exposures function reliably. Failures in execution, delayed rebalancing, or misconfiguration should not cause the hedged portfolio to materially deviate from its intended delta-neutral position.

## Control Modifiers

Control modifiers adjust the base probability of default upward or downward based on systemic factors.

### Jurisdictional clarity

The degree to which the legal and regulatory environment surrounding the issuer, custodians, and exchange relationships is clear and stable. Legal uncertainty or regulatory intervention could disrupt custody, hedging operations, or access to trading venues.

### Governance power distribution and participation

The extent to which control over key protocol parameters and operational configuration is decentralized. High concentration of governance power increases the risk that system-critical changes can be pushed unilaterally or without sufficient oversight.

### Transparency of reserves and hedge positions

The degree to which backing assets and hedge exposures are publicly observable and independently verifiable. Higher transparency reduces the probability that mismatches, shortfalls, or operational failures persist undetected.

### Hedge design robustness

The resilience of the hedging architecture to market structure changes such as basis divergence, imperfect asset matching, funding volatility, or execution slippage. Weak hedge construction increases the probability that the delta-neutral objective breaks.

### Collateral quality safeguards

The strictness of eligibility requirements for assets used as collateral. Assets with higher liquidity, lower volatility, and deeper markets reduce the risk that the backing becomes unstable or difficult to hedge.

### Counterparty diversification safeguards

The degree to which custody, trading, and settlement exposure is distributed across multiple providers and venues. Higher diversification reduces the probability that a single provider failure disrupts the system.

### Margin and liquidation safeguards

The strength of mechanisms designed to prevent forced liquidation of hedge positions. This includes margin buffers, leverage limits, and procedures for handling sudden market moves.

### Redemption and liquidity safeguards

The robustness of the system's ability to contract supply when demand falls. Effective redemption mechanisms and sufficient market liquidity reduce the probability of disorderly deleveraging.

### Operational complexity

The number and interdependence of operational components required for the system to function. Greater complexity increases the probability of operational failure due to coordination errors, infrastructure outages, or misconfiguration.

### Emergency roles risk

The presence and scope of privileged roles capable of bypassing standard operational processes. These roles may mitigate risk by enabling rapid intervention but can also introduce centralization risk or abuse potential.

## Assurance Multipliers

Assurance multipliers reduce (or occasionally increase) the probability of default based on the quality of security and operational controls.

### Audits

List of past audits performed and their coverage.

### Formal verification

Surface covered with formal verification tools.

### Testing

Surface covered with automated tests.

### Monitoring

Whether there are monitoring tools in place for collateral, exchange exposure, hedge drift, operational failures, etc.

### Incidents history

Frequency and track record of previous incidents.

### Operational changes history

Frequency and track record of material changes to custody, exchange integrations, hedging flows, minting/redemption design, or other critical operational infrastructure.

### Smart contract and operational maturity

Time since the protocol has been deployed, plus maturity of the operational model in production.
