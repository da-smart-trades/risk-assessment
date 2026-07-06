# Avalanche C‑Chain — Risk Metrics Reference

**Audience:** Risk analysts, compliance teams, treasury and protocol operations.
**What this document covers:** Every Avalanche C‑Chain metric collected by the Certora Blockchain Risk Assessment platform — what it measures, how it is collected, and how to recognise when it indicates trouble.

> **Reading note.** The Avalanche metric coverage in this product is narrower than the Ethereum or Solana coverage by design. Avalanche uses the **Snowman** consensus algorithm, in which a sampled supermajority quorum confirms each block; finality is achieved within seconds and is not expressed as a sliding "head vs finalized" gap the way Ethereum or Polygon express it. The platform therefore does **not** run a finality workflow for Avalanche — there is no separate `safe` or `finalized` tag to track — and instead focuses on validator decentralization (the meaningful security knob on a Snowman chain), throughput, TVL, and stablecoin activity. Validator data is sourced from the **P‑Chain** (the platform chain that manages staking), even though the metrics in this document describe the **C‑Chain** (the EVM execution environment).

---

## 1. Metric inventory

| Family | What is captured | Collection cadence | Source |
|---|---|---|---|
| Throughput | Transactions per second, blocks per second, mean base fee + median priority fee on the C‑Chain | every **30 min** | Avalanche C‑Chain RPC (`eth_feeHistory`, `eth_getBlockByNumber`) |
| Validator decentralization | Active validator count, total stake (AVAX), Nakamoto‑liveness coefficient, Nakamoto‑safety coefficient, HHI, Renyi entropy (α = 0, 1, 2, ∞), Shapley top three values | every **5 min** | Avalanche P‑Chain RPC (`platform.getCurrentValidators`) |
| Operator decentralization | Top operators by share, entity‑level Nakamoto coefficients, operator coverage % | every **24 h** | Avalanche P‑Chain RPC + curated label set |
| TVL | Total value locked across Avalanche DeFi (USD) | every **1 h** | DefiLlama (`/v2/chains`) |
| Token activity | Inflow, outflow, transaction count, unique addresses, total supply for USDC on Avalanche C‑Chain | every **3 h** | Dune Analytics |

The following workflows are **not** run for Avalanche: finality, time‑to‑finality (soft), governance, releases. Manual operator metrics — including subjective assessments of slashing behaviour, upgrade transparency, or delay‑on‑upgrade — can still be entered under the `GOVERNANCE` category and will appear alongside the automated metrics on the chain detail view.

---

## 2. Why there is no finality metric

Snowman consensus rounds reach probabilistic finality after a sampled supermajority of validators ("Snowball") converges on a block, typically within 1–2 seconds. Avalanche's standard EVM RPC does not expose a distinct `safe` or `finalized` block tag analogous to Ethereum's or Polygon's; any block deeper than the configured `acceptance_threshold` is considered accepted and irreversible by validator software.

Operationally, the proxies for Avalanche finality health are therefore:

- **`blocks_per_second` from the throughput workflow** — measures whether the chain is producing blocks at all.
- **`time_since_last_head` derivable from a manual operator metric** if a team chooses to record it.
- **Validator‑set health from the decentralization workflow** — Snowman's safety guarantees rest on the validator set being well distributed; the canonical "is finality at risk?" question on Avalanche is really "is the validator set degraded?".

Teams that want a finality‑style metric can add a chain‑level manual `GOVERNANCE` entry recording the configured client's accepted‑block depth or external incident notes.

---

## 3. Throughput

Every 30 minutes the platform samples 10 blocks evenly spaced across the prior hour on the C‑Chain to compute network‑wide TPS (sum of transaction counts ÷ wall‑clock span) and BPS (block count over a 5 min lookback), and queries `eth_feeHistory` for the mean base fee and median priority fee.

| Field | Unit |
|---|---|
| `transactions_per_second` | tx/s on the C‑Chain |
| `blocks_per_second` | blocks/s (≈ 0.5 expected — Avalanche's ~2 s block target) |
| `gas_price` | wei — mean base fee + P50 priority fee (in nAVAX × 1e9 conversion) |

### How to know when something is off

- **`blocks_per_second` below ~0.4 for an hour** indicates a meaningful share of slots without an accepted block. Healthy operation centres on ~0.5.
- **`transactions_per_second` collapsing** is meaningful only relative to baseline because Avalanche's per‑subnet activity varies widely. Use **STDDEV_DEVIATION** with at least a 7‑day lookback, not a fixed threshold.
- **Gas‑price spike:** in the C‑Chain's base‑fee regime, sustained 10×+ moves indicate a high‑activity event (e.g., a memecoin launch). A **STDDEV_DEVIATION** rule with `multiplier: 4.0` and `lookback_seconds: 86400` is more robust than a fixed threshold.

---

## 4. Validator decentralization

Every 5 minutes the platform calls `platform.getCurrentValidators` (with `subnetID: null` — the Primary Network) on a configured P‑Chain endpoint. For each validator the platform combines the validator's own stake (`stakeAmount`) with the sum of attached delegations (`delegatorWeight`) to produce a per‑node *total weight*. Weights are normalised from nAVAX to AVAX (`/ 1e9`) and used to compute the standard distribution metrics.

| Metric | What it measures |
|---|---|
| `number_of_nodes` | Count of Primary Network validators with non‑zero total weight |
| `total_amount_of_stakes` | Sum of total weight, denominated in AVAX |
| `nakamoto_liveness_coefficient` | Minimum number of validators whose combined weight exceeds the ⅓ liveness threshold |
| `nakamoto_safety_coefficient` | Minimum number of validators whose combined weight exceeds the ⅔ safety threshold |
| `hhi` | Herfindahl–Hirschman Index of weight share |
| `renyi_entropy_alpha_0/1/2/inf` | Rényi entropies at α = 0 (richness), 1 (Shannon), 2 (collision), ∞ (worst case) |
| `shapley_top_value`, `shapley_second_value`, `shapley_third_value` | Voting power of the top three validators (0–1) |

The stake figure includes delegated weight, which more closely matches what is actually voting in Snowman polls than own‑stake alone.

### How to know when something is off

- **`nakamoto_safety_coefficient` declining**. Avalanche's safety coefficient sits in the low‑to‑mid double digits; a sustained drop into single digits would represent a meaningful centralisation of voting weight.
- **`shapley_top_value` ≥ 0.10** indicates a single validator (with its delegators) is approaching a 10 % share of voting weight on the Primary Network.
- **`total_amount_of_stakes` flat for > 24 h** while `blocks_per_second` continues to advance is almost always an RPC issue, not a real stake change.

Set **STDDEV_DEVIATION** with a 14–30 day lookback and a 1.5–2.0 multiplier to detect structural drift without firing on routine validator churn.

---

## 5. Operator decentralization

Once a day the same `platform.getCurrentValidators` call is paired with the curated Avalanche label set in `metrics/decentralization/operator_labels.json` to produce entity‑level Nakamoto coefficients and top‑operator shares. Many of the largest Primary Network validators are run by named entities (foundations, custodians, professional staking providers); the label set maps `nodeID`s to those entities.

| Field | Meaning |
|---|---|
| `entity_nakamoto_liveness` | Entities whose combined weight exceeds the ⅓ liveness threshold |
| `entity_nakamoto_safety` | Entities whose combined weight exceeds the ⅔ safety threshold |
| `entity_count` | Total distinct labelled operators |
| `coverage_pct` | Fraction of total weight whose operator is identified (0–1) |
| `top_operators[]` | Rank, name, validator count, stake (AVAX), stake share (0–1) |

### How to know when something is off

- **`entity_nakamoto_safety` declining**. Avalanche's entity‑level safety coefficient is structurally lower than its validator‑level coefficient because many `nodeID`s aggregate into a handful of entities. The trend matters more than the absolute number.
- **A single operator's `stake_share` ≥ ⅓** is an immediate liveness concern; ≥ ⅔ is an immediate safety concern.
- **`coverage_pct` falling sharply** usually means new validators came online before the label set was updated.

Operator labels are curated, not RPC‑derived. New large validators may appear briefly as `unlabelled` before being added.

---

## 6. TVL

Hourly poll of `https://api.llama.fi/v2/chains` matched on the display name `Avalanche`. Stored in USD as a `Decimal` for precision.

### How to know when something is off

- **A single‑hour drop of ≥ 10 %** correlates almost always with either a sharp AVAX price move or a DefiLlama re‑adapter event. Inspect the chart legend before assuming a real outflow.
- **DefiLlama outage** appears as a flat line, not a zero.

Recommended alert: **RATE_OF_CHANGE** with `delta_pct: 10`, `window_seconds: 3600`, `direction: below`, severity `WARNING`.

---

## 7. Token activity — USDC on Avalanche

Every three hours a Dune SQL query reports inflow (USD), outflow (USD), unique active addresses, transaction count, and total supply for USDC on Avalanche C‑Chain over a rolling one‑hour window, shifted backward by Dune's ~3 h replication lag.

USDT0 (the LayerZero‑bridged USDT variant) is **not** currently collected on Avalanche; the platform does collect it on Ethereum, Optimism, Ink, Unichain, and Polygon.

### How to know when something is off

- **Outflow exceeding inflow by more than three days' worth of typical net flow** is the canonical leading signal of a depeg or bridge incident. Use **STDDEV_DEVIATION** on the `(outflow − inflow)` series with a multi‑day lookback rather than a fixed dollar threshold.
- **Total supply jumping discontinuously** is either a Circle mint/burn or a Dune query regression. Cross‑check against Circle's published mint/burn ledger.
- **Single‑snapshot drops to zero** are virtually always Dune indexer hiccups. Look at surrounding snapshots before drawing a conclusion.

---

## 8. Alert‑rule mechanics

Every metric in this document can be wrapped in one of three rule kinds. The evaluator runs every 30 seconds with **edge‑trigger** semantics: only state transitions (`OK → TRIGGERED`, `TRIGGERED → RECOVERED`) emit history rows and notifications.

| Rule kind | When it fires |
|---|---|
| **THRESHOLD** | The latest sample crosses a fixed boundary (`>`, `>=`, `<`, `<=`, `==`, `!=`). |
| **RATE_OF_CHANGE** | The latest sample differs from the sample at `now − window_seconds` by more than `delta_pct` percent. |
| **STDDEV_DEVIATION** | The latest sample lies more than `multiplier × σ` from the rolling mean across `lookback_seconds`. Requires ≥ 10 samples; flat series do not fire. |

### Severity tiers

`INFO`, `WARNING`, `CRITICAL` — a label on the notification. The evaluator does not change behaviour by severity; use it for routing.

### Stale‑data handling

If the most recent snapshot is more than **10 minutes old**, the evaluator emits an `ERROR` history row rather than a transition. `ERROR` means *the system could not evaluate*, not *the metric crossed a line*.

---

## 9. Data‑source attribution

| Source | Used for | Notes |
|---|---|---|
| Configured Avalanche C‑Chain RPCs | Throughput | EVM RPC; multi‑endpoint failover |
| Configured Avalanche P‑Chain RPCs | Validator and operator decentralization | `platform.getCurrentValidators`; queries the Primary Network only |
| Curated operator label set | Operator decentralization | Maintained in‑repo |
| DefiLlama (`api.llama.fi/v2/chains`) | TVL | Match on display name `Avalanche` |
| Dune Analytics | Token activity | ~3 h replication lag |

All HTTP calls have a 30 s timeout. All workflow activities are wrapped in Temporal retry policies; persistent failure surfaces as `ERROR` rows in the alert history view so the data gap is visible.

---

*Generated from the Certora Blockchain Risk Assessment platform metric pipeline. Source‑of‑truth code paths: `src/cert_ra/metrics/*` and `src/cert_ra/api/domain/alerts/*`.*
