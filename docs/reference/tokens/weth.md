# WETH Token — Risk Framework Definitions

## Designed Economic Claim

The designed economic claim of WETH is strict 1:1 convertibility with native ETH. Each WETH token represents exactly one unit of ETH that has been deposited into the WETH wrapper contract. Holders must be able to redeem 1 WETH for 1 ETH at any time by unwrapping, with no price exposure, yield component, or collateral structure involved.

## Anchors

Anchors are the fundamental risk drivers. A default occurs when one or more anchors fail.

### Collateral integrity

The wrapper contract must actually hold enough ETH to back emitted WETH. If total WETH supply is not fully matched by ETH held by the contract, the redemption claim fails.

### Redemption functionality

Users must be able to unwrap WETH and receive the corresponding ETH. Even if accounting is correct, WETH defaults structurally if withdrawals cannot be executed as designed.

### Contract accounting correctness

Minting, burning, and balance accounting must correctly track deposits and withdrawals so that user balances, total supply, and contract ETH remain consistent.

### ERC-20 functionality integrity

Because WETH's utility depends on being the standard ERC-20 form of ETH in DeFi, transfers and approvals must function reliably. If token mechanics fail materially, WETH may remain backed but no longer uphold its intended usable ERC-20 claim.

## Control Modifiers

Control modifiers adjust the base probability of default upward or downward based on systemic factors.

### Contract immutability

If the wrapper is immutable, the risk of discretionary rule changes is lower. If upgradeable or admin-controlled, anchor failure probability increases.

### Code simplicity

WETH is structurally stronger when the contract remains minimal and narrowly scoped. More logic, hooks, or extensions increase implementation and failure surface.

### Integration complexity

The degree of difficulty for external systems to correctly support the token. Tokens that follow widely adopted standards and have simple interaction patterns are easier for wallets, exchanges, and DeFi protocols to integrate safely.

## Assurance Multipliers

Assurance multipliers reduce (or occasionally increase) the probability of default based on the quality of security and operational controls.

### Audits

List of past audits performed and their coverage.

### Formal verification

Surface covered with formal verification tools.

### Testing

Surface covered with automated tests.

### Smart contract maturity

Time since the contract has been deployed in production.
