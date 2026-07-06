# LINK Token — Risk Framework Definitions

## Designed Economic Claim

LINK is the native utility and security token of the Chainlink network. It is used to pay for Chainlink services and forms the backbone of the staking economy of the oracle network.

## Anchors

Anchors are the fundamental risk drivers. A default occurs when one or more anchors fail.

### Native payment token

LINK must remain economically necessary to the Chainlink network, embedded in the payments flow. If the network is allowed to scale by bypassing this requirement, the token's core claim is affected.

### Oracle services continuity

Chainlink's oracle and related services must remain in demand by consumers. LINK depends on real, sustained usage of the network.

### Security integrity

The economic incentives implemented for the Chainlink network must push participants toward honest behavior. If the security layer is weak, LINK will not function as a security asset.

### Token integrity

The LINK token implementation must remain technically reliable. Failures in the smart contracts backing the system can break the token's economic claim.

### Governance integrity

The entities controlling protocol settings, incentives, and token-related design choices must not undermine LINK's intended role.

## Control Modifiers

Control modifiers adjust the base probability of default upward or downward based on systemic factors.

### Jurisdictional and regulatory exposure

The degree to which legal or regulatory actions could impair critical system components such as custody, exchange access, or counterparty relationships. Higher exposure increases the probability of disruption to core operations.

### Governance power concentration

The extent to which control over critical system parameters and operational decisions is concentrated in a small set of actors. Higher concentration increases the risk of unilateral or poorly vetted changes affecting system integrity.

### Operational complexity

The number of moving parts and coordination requirements needed for the system to function correctly. Higher complexity increases the probability of execution failure.

### Privileged roles

The presence, scope, and control of actors capable of overriding normal system behavior. These roles can reduce or increase risk depending on their design, but always introduce discretionary risk.

### Mechanism robustness

The structural soundness of the core mechanism under realistic market conditions, including stress scenarios. Weak or assumption-heavy designs increase failure probability.

### Input quality and dependency robustness

The reliability and quality of the inputs the system depends on (e.g., data sources, price feeds, exchanges). Poor-quality or fragile inputs increase the likelihood of system instability.

### Dependency diversification

The extent to which critical dependencies (custodians, exchanges, infrastructure providers) are distributed across independent actors. Low diversification increases single-point-of-failure risk.

## Assurance Multipliers

Assurance multipliers reduce (or occasionally increase) the probability of default based on the quality of security and operational controls.

### Audits

List of past audits performed and their coverage.

### Formal verification

Surface covered with formal verification tools.

### Testing

Surface covered with automated tests.

### Monitoring

Whether monitoring tools are in place for collateral, exchange exposure, operational failures, etc.

### Incidents history

Frequency and track record of previous incidents.

### Operational changes history

Frequency and track record of material changes to custody, exchange integrations, or other critical operational infrastructure.

### Smart contract and operational maturity

Time since the protocol has been deployed, plus maturity of the operational model in production.
