# stETH Token — Risk Framework Definitions

## Designed Economic Claim

stETH is a liquid staking token issued by Lido that represents ETH deposited into Ethereum's proof-of-stake system through the protocol. It is intended to track a proportional claim on the underlying staked ETH plus accrued staking rewards, while remaining transferable and usable in DeFi.

## Anchors

Anchors are the fundamental risk drivers. A default occurs when one or more anchors fail.

### Validator performance and slashing resilience

The Ethereum validators operated through Lido must remain active, performant, and avoid significant slashing penalties. If validators underperform, go offline, or are slashed at scale, the underlying ETH backing stETH is reduced, breaking the proportional claim.

### Node operator set integrity

The set of node operators managing validators must remain reliable, sufficiently decentralized, and non-collusive. If the operator set becomes compromised, highly correlated, or poorly managed, it can lead to systemic validator failures and loss of funds.

### Accounting and oracle correctness

The protocol must correctly track validator balances, rewards, and penalties, and reflect them accurately in stETH supply and user balances. This depends on correct oracle reporting and accounting logic. If this mechanism fails, stETH may no longer represent an accurate claim on the underlying ETH.

### Smart contract and protocol integrity

The smart contracts governing deposits, staking flows, accounting updates, and withdrawals must function correctly and securely. Failures in this layer — including bugs, exploits, or incorrect execution of deposits or withdrawals — can lead to loss of funds, mis-accounting, or inability to redeem stETH for ETH, breaking the token's economic claim.

## Control Modifiers

Control modifiers adjust the base probability of default upward or downward based on systemic factors.

### Jurisdictional and regulatory exposure

The degree to which regulatory actions could affect node operators, governance participants, or infrastructure providers involved in Lido. Restrictions on validator operations or participation could impair staking activity and affect the ETH backing stETH.

### Governance power distribution and participation

The extent to which governance power is distributed among LDO holders and actively exercised. Concentration of voting power or low participation increases the risk of decisions that negatively impact protocol integrity.

### Voting power integrity

The resistance of the governance system to manipulation of voting power, including delegation mechanics, token concentration, and the potential use of borrowed voting power. Weak integrity increases the risk of governance capture.

### Governance mechanism robustness

The robustness of the governance process, including quorum requirements, voting thresholds, timelocks, and execution mechanisms. Weak design increases the risk of rushed, low-quality, or adversarial changes to the protocol.

### Governance control scope and safeguards

The scope of protocol components that can be modified through governance — including node operator set, oracle configuration, and smart contract upgrades — and the safeguards in place to control such changes. Weak safeguards increase the risk of harmful modifications to critical system components.

### Emergency roles risk

The presence and scope of privileged roles capable of bypassing or accelerating normal governance processes, such as multisigs or pause mechanisms. While useful in incident response, these roles introduce discretionary control risk.

### Operational complexity

The number of interacting components required for the system to function correctly, including validators, node operators, oracle reporting, rebasing, and withdrawals. Higher complexity increases the probability of coordination failures and implementation errors.

### Node operator management quality

The effectiveness of processes used to select, monitor, and replace node operators, including performance tracking and response to underperformance. Weak management increases the likelihood of validator-related failures.

### Oracle design and reporting robustness

The robustness of the oracle system responsible for reporting validator balances and updating stETH supply, including quorum design, update frequency, and resistance to faulty or malicious inputs. Weak oracle design increases the risk of incorrect accounting.

### Dependency diversification

The extent to which critical roles — particularly node operators and oracle participants — are distributed across independent actors, infrastructures, and client implementations. Low diversification increases systemic and correlated failure risk.

## Assurance Multipliers

Assurance multipliers reduce (or occasionally increase) the probability of default based on the quality of security and operational controls.

### Audits

List of past audits performed on Lido's smart contracts and protocol components, including coverage of staking, oracle, and withdrawal systems.

### Formal verification

Surface of the protocol that has been formally verified, particularly critical components related to accounting, fund flows, and withdrawal logic.

### Testing

Extent of automated testing coverage across core protocol components, including deposits, rebasing, oracle updates, and withdrawals.

### Monitoring

Availability of monitoring tools and transparency into validator performance, staking balances, oracle reports, and withdrawal queues.

### Incidents history

Track record of past incidents, including validator slashing events, oracle inconsistencies, or stress events affecting stETH, and how effectively they were handled.

### Operational changes history

Frequency and impact of changes to critical components such as node operator sets, oracle configuration, contract upgrades, and withdrawal mechanisms.

### Smart contract and operational maturity

Time the protocol has been live in production and the maturity of its staking, accounting, and withdrawal systems under real-world conditions.
