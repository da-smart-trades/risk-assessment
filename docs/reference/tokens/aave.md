# AAVE Token — Risk Framework Definitions

## Designed Economic Claim

The AAVE token is a governance token. Its designed economic claim is the ability for AAVE holders to decide and execute changes over the Aave protocol — upgrades, parameter adjustments, treasury management, and other governance actions.

## Anchors

Anchors are the fundamental risk drivers. A default occurs when one or more anchors fail.

### Governance integrity & liveness

The governance process is hard to capture or control by a malicious entity. The feasibility of such attacks depends on the actual decentralization level and the governance parameters configured (threshold, quorum, delays, etc.).

### Control-plane integrity

The set of configurations ruling the protocol itself can only be managed by the governance system in place. This mechanism cannot be broken or bypassed — no privileged shortcut should be able to override these variables.

### Treasury custody integrity

The list of assets controlled by governance cannot be expropriated except through the expected governance process, and there are sufficient controls in place to prevent fast theft.

## Control Modifiers

Control modifiers adjust the base probability of default upward or downward based on systemic factors.

### Jurisdictional clarity

Ensuring a clear regulatory scenario means a safe environment for token holders — decentralization cannot be affected by legal action against identifiable controllers.

### Governance power distribution and participation

How decentralized governance is in practice: token distribution, concentration of delegated voting power, quorum hit rate, and related factors.

### Voting power integrity

How hard it is to manipulate voting power through delegation mechanics, wrapper delegation loopholes, or flash-loanable voting.

### Voting mechanism integrity

How hard it is to manipulate the logic implementing voting or the proposal lifecycle.

### Governance parameters safeguards

How hard it is to change governance core parameters such as threshold, quorum, voting duration, and execution delays.

### Protocol parameters safeguards

Protocol parameters can be controlled by governance. How hard it is to change a configuration that would make the protocol behave differently.

### Protocol upgrades safeguards

Upgrades can be rolled out by governance. How hard it is to change a piece of logic that would modify the behavior of the protocol itself.

### Emergency roles risk

The presence and scope of potential emergency roles that can bypass the governance execution process can affect decentralization.

### Protocol complexity

The larger the protocol — in terms of markets and chains — the greater the number of variables to be configured, increasing operational risk.

### Treasury custody risk

How easily governance can move or expropriate treasury assets.

## Assurance Multipliers

Assurance multipliers reduce (or occasionally increase) the probability of default based on the quality of security and operational controls.

### Audits

List of past audits performed and their coverage.

### Formal verification

Surface covered with formal verification tools.

### Testing

Surface covered with automated tests.

### Monitoring

Whether monitoring tools are in place (e.g., Hypernative).

### Incidents history

Frequency and track record of previous incidents.

### Upgrades history

Frequency and track record of past upgrades.

### Smart contract maturity

Time since the protocol has been deployed in production.
