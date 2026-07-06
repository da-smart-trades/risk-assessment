# Solana (Mainnet Beta) — Risk Metrics Reference

**Audience:** Risk analysts, compliance teams, treasury and protocol operations.
**What this document covers:** Every Solana L1 metric collected by the Certora Blockchain Risk Assessment platform — what it measures, how it is collected, and how to recognise when it indicates trouble.

> **Reading note.** Solana's safety and progress model is fundamentally different from Ethereum's. Solana uses a three‑stage *commitment* pipeline (processed → confirmed → finalized) and produces a new slot every ≈ 400 ms with target finality on the order of a few seconds. Where Ethereum metrics talk about epochs and Casper finality, the Solana equivalents talk about slot lag and supermajority rooting.

---

## 1. Metric inventory

| Family | What is captured | Collection cadence | Source |
|---|---|---|---|
| Finality | Processed, confirmed, finalized slot numbers; processed→confirmed and confirmed→finalized slot gaps | every **30 s** | Solana RPC (`getSlot` with commitment levels) |
| Time‑to‑finality (soft) | Average inter‑arrival time between new slot notifications | every **10 min** | Solana RPC WebSocket (`slot_subscribe`) |
| Throughput | Average transactions per second, blocks per second, mean transaction fee | every **30 min** | Dune SQL on `solana.transactions` |
| Validator decentralization | Active vote‑account count, total activated stake (SOL), Nakamoto‑liveness coefficient, Nakamoto‑safety coefficient, HHI, Renyi entropy (α = 0, 1, 2, ∞), Shapley top three values | every **5 min** | Solana RPC (`getVoteAccounts`) |
| Operator decentralization | Top operators by stake share, entity‑level Nakamoto coefficients, operator coverage % | every **24 h** | `getVoteAccounts` + curated operator label set |
| Governance | Count of open SIMD pull requests | every **6 h** | GitHub (`solana-foundation/solana-improvement-documents`) |
| TVL | Total value locked across Solana DeFi (USD) | every **1 h** | DefiLlama (`/v2/chains`) |
| Token activity | Inflow, outflow, transaction count, unique addresses, total supply for USDC on Solana | every **3 h** | Dune Analytics (Solana token tables) |

Manual operator metrics under the `GOVERNANCE` category (e.g., delay‑on‑upgrade, slashing behaviour, upgrade transparency) can be added to complement the automated feeds on the chain detail view.

---

## 2. Finality

Solana does not have a single "head" pointer. Validators expose three commitment levels over RPC:

- **`processed`** — slot the validator just produced or saw. No fork resolution. Equivalent to "the chain is showing me this slot right now".
- **`confirmed`** — slot that has supermajority vote attestations from the cluster. Reorgs above this level are theoretically possible but rare in practice.
- **`finalized`** ("rooted") — slot that has been *rooted* by a supermajority and is considered irreversible by validator software.

### Stored fields

| Field | Meaning |
|---|---|
| `processed_slot` | Latest slot the queried RPC has observed |
| `confirmed_slot` | Slot with supermajority vote attestations |
| `finalized_slot` | Slot rooted by supermajority — the platform's irreversibility marker |
| `processed_confirmed_gap` | `processed_slot − confirmed_slot` |
| `confirmed_finalized_gap` | `confirmed_slot − finalized_slot` |

### How it is collected

A single polling cycle issues three parallel `getSlot` calls with commitments `processed`, `confirmed`, and `finalized`. The platform tries each configured RPC endpoint in order until one returns all three slots successfully. Failure across all configured endpoints raises and Temporal retries on the next 30 s tick.

### How to know when something is off

| Signal | Interpretation |
|---|---|
| `confirmed_finalized_gap > ~32 slots` for several consecutive snapshots | Healthy operation typically sits in the 20–35 slot range (≈ 8–14 s). Persistent growth above ~40 slots means rooting is lagging and the chain is taking measurably longer to reach irreversibility. |
| `confirmed_finalized_gap > 100 slots` | Strong indication that a supermajority of validators is not rooting in time — either a software regression, a network partition, or a coordinated outage. Investigate immediately. |
| `processed_confirmed_gap > ~10 slots` sustained | Vote propagation is slow. Often the leading edge of a leader‑skip event or a cluster‑wide TPU congestion incident. |
| Snapshots stuck at the same `processed_slot` | The queried RPC fell behind. Compare against a public block explorer before concluding the chain is degraded. |
| Snapshot age > 10 min in alert evaluator | The alert engine emits an `ERROR` row. Treat as a data‑quality issue first, not a chain incident. |

### Recommended alert rules

- **THRESHOLD `>`** on `confirmed_finalized_gap` with `value: 100`, severity `CRITICAL`.
- **THRESHOLD `>`** on `processed_confirmed_gap` with `value: 20`, severity `WARNING`.
- **STDDEV_DEVIATION** on `confirmed_finalized_gap` with `multiplier: 3.0`, `lookback_seconds: 21600`, `direction: above` — catches sustained, statistical regressions even when the absolute value sits below the hard threshold.

---

## 3. Time‑to‑finality (soft)

A WebSocket subscription to `slot_subscribe` captures three consecutive slot notifications and stores the mean of the inter‑arrival gaps. This measures *how fast the cluster is producing slots*, not how fast irreversible state is being committed.

Expected value: ≈ **0.4 s** (Solana's slot time). Sustained values above ~0.6 s indicate measurable skipped‑slot rates. Values above 1 s for 30+ minutes mean either the cluster itself is degraded or the RPC endpoint is silently buffering notifications. Cross‑check against §2's `processed_slot` advancement before drawing conclusions.

Recommended alert: **STDDEV_DEVIATION** with `multiplier: 3.0`, `lookback_seconds: 21600`, `direction: above`.

---

## 4. Throughput

Solana's transaction throughput is too high to sample by polling individual blocks the way Ethereum does. Instead, every 30 minutes the platform runs a SQL query against Dune's `solana.transactions` table over the prior hour:

```
SELECT
  AVG(fee)                                AS avg_gas_price,
  (MAX(block_slot) - MIN(block_slot))
    / DATE_DIFF('second', MIN(block_time), MAX(block_time)) AS blocks_per_second,
  COUNT(*)
    / DATE_DIFF('second', MIN(block_time), MAX(block_time)) AS transactions_per_second
FROM solana.transactions
WHERE block_time >= NOW() - INTERVAL '1' HOUR
```

| Field | Unit |
|---|---|
| `transactions_per_second` | tx/s (cluster‑wide; includes vote transactions, which dominate the count) |
| `blocks_per_second` | slots/s (≈ 2.5 expected) |
| `gas_price` | lamports — mean transaction fee |

### How to know when something is off

- **`blocks_per_second` falling below ~1.8** for an hour or more usually means a non‑trivial skipped‑slot rate. Solana publishes target ≈ 2.5 slots/s.
- **`transactions_per_second` collapsing** is meaningful only relative to baseline because vote transactions vary with active validator count. Use **STDDEV_DEVIATION** with a multi‑day lookback, not a fixed threshold.
- **Dune lag.** This pipeline is driven by Dune, which trails the chain by approximately 3 hours. A snapshot timestamped 12:00 UTC reflects the 08:00–09:00 UTC window. Treat low recent samples as upstream lag first.

---

## 5. Validator decentralization

Every 5 minutes the platform calls `getVoteAccounts` with `commitment=finalized` and uses the returned activated‑stake values to compute distribution metrics. Stakes are normalised to SOL (`lamports / 1e9`).

| Metric | What it measures |
|---|---|
| `number_of_nodes` | Count of vote accounts with non‑zero activated stake |
| `total_amount_of_stakes` | Sum of activated stake, denominated in SOL |
| `nakamoto_liveness_coefficient` | Minimum number of validators whose combined stake exceeds the ⅓ liveness threshold |
| `nakamoto_safety_coefficient` | Minimum number of validators whose combined stake exceeds the ⅔ safety threshold |
| `hhi` | Herfindahl–Hirschman Index of stake share |
| `renyi_entropy_alpha_0/1/2/inf` | Rényi entropies at α = 0 (richness), 1 (Shannon), 2 (collision), ∞ (worst case) |
| `shapley_top_value`, `shapley_second_value`, `shapley_third_value` | Voting power of the top three validators (0–1) |

### How to know when something is off

These metrics drift slowly. Use **STDDEV_DEVIATION** with a 14‑ to 30‑day lookback and a 1.5–2.0 multiplier to detect structural drift; on Solana the day‑to‑day volatility is low enough that even a 1 σ deviation can be meaningful.

Concrete red flags:

- **`nakamoto_safety_coefficient` declining**. Solana's `nakamoto_safety_coefficient` has historically sat in the low 20s; any sustained move below 20 is unusual and a meaningful concentration signal.
- **`shapley_top_value` ≥ 0.10**. No single validator should approach a 10 % share of cluster stake. A move past this number is a centralisation flag in its own right.
- **`total_amount_of_stakes` flat for > 24 h** while `processed_slot` continues to advance is almost always an RPC issue, not a real stake change.

---

## 6. Operator decentralization

`getVoteAccounts` returns one entry per *vote account*, not per operator. Once a day, the platform aggregates vote accounts into operators using the curated Solana label set under `metrics/decentralization/operator_labels.json` and stores entity‑level Nakamoto coefficients, top‑operator shares, and a `coverage_pct` indicating what fraction of activated stake is labelled.

| Field | Meaning |
|---|---|
| `entity_nakamoto_liveness` | Entities whose combined stake exceeds the ⅓ liveness threshold |
| `entity_nakamoto_safety` | Entities whose combined stake exceeds the ⅔ safety threshold |
| `entity_count` | Total distinct labelled operators |
| `coverage_pct` | Fraction of activated stake whose operator is identified (0–1) |
| `top_operators[]` | Rank, name, validator count, stake (SOL), stake share (0–1) |

### How to know when something is off

- **`entity_nakamoto_safety` dropping into single digits.** At single digits the cost of a coordinated ⅔ attack falls into the range of "a handful of named entities". Set a `THRESHOLD` alert on a level your team can justify.
- **A single operator's `stake_share` ≥ ⅓** is an immediate liveness concern; ≥ ⅔ is an immediate safety concern. Set `THRESHOLD` rules on the top‑operator share directly.
- **`coverage_pct` dropping** does not mean decentralization improved — it usually means new validators came online that the label set has not yet annotated. Update the label set rather than acting on the chart.

Operator labels are curated, not RPC‑derived. New large validators may appear briefly as `unlabelled` before being added to the label set.

---

## 7. Governance

Solana has no on‑chain governance. The platform tracks the SIMD (Solana Improvement Document) process by counting **open pull requests** in `solana-foundation/solana-improvement-documents`. A rising count over weeks suggests active proposal activity; a falling count suggests merges or closures.

Use SIMD counts as situational context for chain upgrades, not as a hard signal. A sudden swing is rarely a real network event; it is usually a batch of cleanups or a synchronization with an upcoming release branch.

---

## 8. TVL

Hourly poll of `https://api.llama.fi/v2/chains`. The DefiLlama payload contains one entry per chain (matched on `Solana`); the `tvl` field is stored in USD.

### How to know when something is off

- **A single‑hour drop of ≥ 10 %** on a chain the size of Solana is rare. It usually correlates with either a SOL price move that re‑prices SOL‑denominated TVL or a DefiLlama re‑adapter event. Inspect the chart legend and the SOL/USD chart before assuming a real outflow.
- **DefiLlama outage** appears as a flat line, not a zero. The fetcher raises on HTTP failure and Temporal retries, so a true `0` row should never appear unless DefiLlama legitimately returned one.

Recommended alert: **RATE_OF_CHANGE** with `delta_pct: 10`, `window_seconds: 3600`, `direction: below`, severity `WARNING`.

---

## 9. Token activity — USDC on Solana

Every three hours a Dune SQL query reports inflow (USD), outflow (USD), unique active addresses, transaction count, and total supply for USDC on Solana over a rolling one‑hour window, shifted backward by Dune's ~3 h replication lag.

### How to know when something is off

- **Outflow exceeding inflow by more than three days' worth of typical net flow** in a single window is the canonical leading signal of a depeg or bridge incident on USDC. Use **STDDEV_DEVIATION** on the `(outflow − inflow)` series with a multi‑day lookback rather than a fixed dollar threshold.
- **Total supply jumping discontinuously** is either a Circle mint/burn or a Dune query regression. Cross‑check against Circle's published mint/burn ledger before treating it as a chain event.
- **Single‑snapshot drops to zero** are virtually always Dune indexer hiccups. Look at the surrounding snapshots before drawing a conclusion.

---

## 10. Alert‑rule mechanics

Every metric in this document can be wrapped in one of three rule kinds. The evaluator runs every 30 seconds with **edge‑trigger** semantics: only state transitions (`OK → TRIGGERED`, `TRIGGERED → RECOVERED`) emit history rows and notifications.

| Rule kind | When it fires |
|---|---|
| **THRESHOLD** | The latest sample crosses a fixed boundary (`>`, `>=`, `<`, `<=`, `==`, `!=`). Best for hard limits with operational meaning, e.g., `confirmed_finalized_gap > 100`. |
| **RATE_OF_CHANGE** | The latest sample differs from the sample at `now − window_seconds` by more than `delta_pct` percent. Best for "the chain changed faster than is normal". |
| **STDDEV_DEVIATION** | The latest sample lies more than `multiplier × σ` from the rolling mean across `lookback_seconds`. Best for slowly‑drifting metrics whose normal range itself shifts. Requires at least 10 samples; flat series do not fire. |

### Severity tiers

`INFO`, `WARNING`, `CRITICAL` — purely a label on the notification. The evaluator does not change behaviour by severity; use it for routing.

### Stale‑data handling

If the most recent snapshot is more than **10 minutes old**, the evaluator emits an `ERROR` history row rather than a transition. `ERROR` means *the system could not evaluate*, not *the metric crossed a line*. Investigate the collector before assuming the chain is degraded.

---

## 11. Data‑source attribution

| Source | Used for | Notes |
|---|---|---|
| Configured Solana RPCs | Finality, validator decentralization, time‑to‑finality | Multi‑endpoint failover; ordered list, first success wins |
| Curated operator label set | Operator decentralization | Solana has no equivalent of Rated; labels are maintained in‑repo |
| GitHub (`api.github.com`) | Governance SIMD count | Unauthenticated quota 1,500/h; token quota 5,000/h |
| DefiLlama (`api.llama.fi/v2/chains`) | TVL | Match on display name `Solana` |
| Dune Analytics | Throughput, token activity | ~3 h replication lag; queries shift their windows accordingly |

All HTTP calls have a 30 s timeout. All workflow activities are wrapped in Temporal retry policies; persistent failure surfaces as `ERROR` rows in the alert history view so the data gap is visible to teams using the metric.

---

*Generated from the Certora Blockchain Risk Assessment platform metric pipeline. Source‑of‑truth code paths: `src/cert_ra/metrics/*` and `src/cert_ra/api/domain/alerts/*`.*
