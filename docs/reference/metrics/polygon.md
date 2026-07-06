# Polygon PoS — Risk Metrics Reference

**Audience:** Risk analysts, compliance teams, treasury and protocol operations.
**What this document covers:** Every Polygon PoS metric collected by the Certora Blockchain Risk Assessment platform — what it measures, how it is collected, and how to recognise when it indicates trouble.

> **Reading note.** Polygon PoS finality is anchored by a separate **Heimdall** consensus layer that periodically writes checkpoints to Ethereum L1. The execution layer (Bor) tags blocks `latest` and `finalized` to surface this; the platform reads those tags directly and does *not* attempt to interpret the checkpoint contract on its own. There is no `safe` stage on Polygon — unlike Ethereum and the EVM L2s.

---

## 1. Metric inventory

| Family | What is captured | Collection cadence | Source |
|---|---|---|---|
| Finality | Latest block height, finalized block height, latest→finalized block gap, time since last head | every **30 s** | Polygon RPC (`eth_getBlockByNumber`) |
| Throughput | Transactions per second, blocks per second, mean base fee + median priority fee | every **30 min** | Polygon RPC (`eth_feeHistory`, `eth_getBlockByNumber`) |
| Validator decentralization | Active validator count, total stake (POL), Nakamoto‑liveness coefficient, Nakamoto‑safety coefficient, HHI, Renyi entropy (α = 0, 1, 2, ∞), Shapley top three values | every **5 min** | Polygon Staking API v2 |
| Operator decentralization | Top operators by share, entity‑level Nakamoto coefficients, operator coverage % | every **24 h** | Polygon Staking API v2 + curated label overrides |
| TVL | Total value locked across Polygon DeFi (USD) | every **1 h** | DefiLlama (`/v2/chains`) |
| Token activity | Inflow, outflow, transaction count, unique addresses, total supply for USDC and USDT0 on Polygon | every **3 h** | Dune Analytics |

Polygon is **not** in the time‑to‑finality (soft) workflow, the governance workflow, or any token activity feed beyond USDC and USDT0. Manual operator metrics under the `GOVERNANCE` category can be added to complement the automated feeds on the chain detail view.

---

## 2. Finality

Bor (the execution layer) exposes two block tags relevant to risk analysis:

- **`latest`** — the most recent block the queried node has imported.
- **`finalized`** — the most recent block that Heimdall has checkpointed to Ethereum L1 and that Bor considers irreversible by virtue of that anchor.

There is no `safe` tag. The "soft" view and the "hard" view are the same.

### Stored fields

| Field | Meaning |
|---|---|
| `latest_height` | Latest block imported by the queried Polygon RPC |
| `finalized_height` | Most recent block finalized via Heimdall checkpoint to Ethereum |
| `latest_to_finalized_blocks` | `latest_height − finalized_height`. This is the *L1 anchor lag* expressed in Polygon blocks. |
| `time_since_last_head` | Wall‑clock seconds since `latest.timestamp`, measuring how recently the queried node imported a block |

### How it is collected

Each polling cycle issues two parallel `eth_getBlockByNumber` calls (`latest`, `finalized`) against the configured Polygon RPC pool. If every endpoint fails the workflow raises and Temporal retries on the next 30 s tick.

### How to know when something is off

| Signal | Interpretation |
|---|---|
| `latest_to_finalized_blocks` rising steadily over many snapshots | Heimdall is failing to submit checkpoints to Ethereum L1, either because Ethereum itself is congested, the checkpoint signer set is degraded, or the Polygon→Ethereum bridge contract is paused. This is the most important finality signal on Polygon. |
| `latest_to_finalized_blocks > ~1,000 blocks` | Approximately 30+ minutes of un‑checkpointed history. Polygon's healthy checkpoint cadence is one every ~30 minutes; a backlog of more than one missed checkpoint warrants investigation. |
| `time_since_last_head > 30 s` | The queried RPC is more than several blocks behind. Polygon block time is ~2 seconds, so anything past ~10 s is already noticeable; > 30 s sustained means the RPC, not the chain, is the problem most of the time. |
| Snapshots stuck at the same `latest_height` | The RPC fell behind. Compare against a public block explorer before concluding the chain is degraded. |
| Snapshot age > 10 min in alert evaluator | The alert engine emits an `ERROR` row. Treat as a data‑quality issue first, not a chain incident. |

### Recommended alert rules

- **THRESHOLD `>`** on `latest_to_finalized_blocks` with `value: 1000`, severity `WARNING`.
- **THRESHOLD `>`** on `latest_to_finalized_blocks` with `value: 2500`, severity `CRITICAL`.
- **THRESHOLD `>`** on `time_since_last_head` with `value: 60`, severity `WARNING`.

---

## 3. Throughput

Every 30 minutes the platform samples 10 blocks evenly spaced across the prior hour to compute TPS and BPS, and queries `eth_feeHistory` for the mean base fee and median priority fee.

| Field | Unit |
|---|---|
| `transactions_per_second` | tx/s |
| `blocks_per_second` | blocks/s (≈ 0.5 expected — Polygon's ~2 s block time) |
| `gas_price` | wei — mean base fee + P50 priority fee |

### How to know when something is off

- **BPS deviation:** healthy Polygon sits near 0.5 blocks/s. Drops below 0.4 for an hour indicate a meaningful share of missed blocks.
- **TPS collapse:** Polygon throughput varies significantly by use‑case mix; absolute thresholds rarely transfer between weeks. Use **STDDEV_DEVIATION** with at least a 7‑day lookback rather than a fixed value.
- **Gas‑price spike:** Polygon fees are usually small (low gwei). A spike of 10×+ that persists across multiple snapshots usually correlates with a memecoin or NFT mint event; a **STDDEV_DEVIATION** alert with `multiplier: 4.0` and `lookback_seconds: 86400` is more robust than a fixed threshold.

---

## 4. Validator decentralization

Every 5 minutes the platform pages through `https://staking-api.polygon.technology/api/v2/validators`, normalises `total_staked` from wei to POL (`/ 1e18`), and computes the standard distribution metrics.

| Metric | What it measures |
|---|---|
| `number_of_nodes` | Count of validators with non‑zero total stake |
| `total_amount_of_stakes` | Sum of total stake, denominated in POL |
| `nakamoto_liveness_coefficient` | Minimum number of validators whose combined stake exceeds the ⅓ liveness threshold |
| `nakamoto_safety_coefficient` | Minimum number of validators whose combined stake exceeds the ⅔ safety threshold |
| `hhi` | Herfindahl–Hirschman Index of stake share |
| `renyi_entropy_alpha_0/1/2/inf` | Rényi entropies at α = 0 (richness), 1 (Shannon), 2 (collision), ∞ (worst case) |
| `shapley_top_value`, `shapley_second_value`, `shapley_third_value` | Voting power of the top three validators (0–1) |

### How to know when something is off

Polygon's validator set is small (low hundreds) relative to Ethereum or Solana, which means individual entrants and exits move the metrics more. Set a **STDDEV_DEVIATION** rule with a 14‑day lookback and a 2.0 multiplier to detect structural change without firing on routine validator churn.

Concrete red flags:

- **`nakamoto_safety_coefficient` declining**. On Polygon this number is structurally lower than on Ethereum or Solana — single digits are normal — so use direction, not level, as the signal.
- **`shapley_top_value` ≥ 0.10** indicates a single validator is approaching a 10 % share of stake. This is unusual on Polygon and worth investigating in the operator view.
- **Polygon Staking API returning `success=false`** is treated as a hard failure and the workflow raises; you will see no snapshot for that cycle rather than a misleading zero.

---

## 5. Operator decentralization

Once a day the same Polygon Staking API call is used as the operator source: the API exposes operator names and owner addresses per validator, and the curated label set in `metrics/decentralization/operator_labels.json` overrides where the API name is missing or generic. Entity‑level Nakamoto coefficients, top‑operator shares, and a `coverage_pct` are stored.

| Field | Meaning |
|---|---|
| `entity_nakamoto_liveness` | Entities whose combined stake exceeds the ⅓ liveness threshold |
| `entity_nakamoto_safety` | Entities whose combined stake exceeds the ⅔ safety threshold |
| `entity_count` | Total distinct labelled operators |
| `coverage_pct` | Fraction of total stake whose operator is identified (0–1) |
| `top_operators[]` | Rank, name, validator count, stake (POL), stake share (0–1) |

### How to know when something is off

- **`entity_nakamoto_safety` declining into low single digits** means the cost of a coordinated ⅔ attack falls into the range of "a handful of named entities". Polygon is structurally more centralised than the other L1s in this document; calibrate thresholds to your team's risk tolerance.
- **A single operator's `stake_share` ≥ ⅓** is an immediate liveness concern; ≥ ⅔ is an immediate safety concern.
- **`coverage_pct` falling sharply** usually means new validators came online before the label set was updated.

---

## 6. TVL

Hourly poll of `https://api.llama.fi/v2/chains` matched on the display name `Polygon`. Stored in USD as a `Decimal` for precision.

### How to know when something is off

- **A single‑hour drop of ≥ 10 %** correlates almost always with either a sharp move in MATIC/POL price or a DefiLlama re‑adapter event. Inspect the chart legend before assuming a real outflow.
- **DefiLlama outage** appears as a flat line, not a zero.

Recommended alert: **RATE_OF_CHANGE** with `delta_pct: 10`, `window_seconds: 3600`, `direction: below`, severity `WARNING`.

---

## 7. Token activity — USDC and USDT0 on Polygon

Every three hours Dune SQL queries report inflow (USD), outflow (USD), unique active addresses, transaction count, and total supply for USDC and USDT0 on Polygon over a rolling one‑hour window, shifted backward by Dune's ~3 h replication lag.

### How to know when something is off

- **Outflow exceeding inflow by more than three days' worth of typical net flow** is the canonical leading signal of a depeg or bridge incident. Use **STDDEV_DEVIATION** on the `(outflow − inflow)` series with a multi‑day lookback rather than a fixed dollar threshold.
- **Total supply jumping discontinuously** is either a Circle / Tether mint/burn or a Dune query regression. Cross‑check against the issuer's published mint/burn ledger.
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
| Configured Polygon (Bor) RPCs | Finality, throughput | Multi‑endpoint failover; ordered list |
| `staking-api.polygon.technology/api/v2/validators` | Validator and operator decentralization | Paginated; failure raises so no misleading zero rows appear |
| DefiLlama (`api.llama.fi/v2/chains`) | TVL | Match on display name `Polygon` |
| Dune Analytics | Token activity | ~3 h replication lag |

All HTTP calls have a 30 s timeout. All workflow activities are wrapped in Temporal retry policies; persistent failure surfaces as `ERROR` rows in the alert history view so the data gap is visible.

---

*Generated from the Certora Blockchain Risk Assessment platform metric pipeline. Source‑of‑truth code paths: `src/cert_ra/metrics/*` and `src/cert_ra/api/domain/alerts/*`.*
