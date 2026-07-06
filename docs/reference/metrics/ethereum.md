# Ethereum (Mainnet) — Risk Metrics Reference

**Audience:** Risk analysts, compliance teams, treasury and protocol operations.
**What this document covers:** Every Ethereum L1 metric collected by the Certora Blockchain Risk Assessment platform — what it measures, how it is collected, and how to recognise when it indicates trouble.

---

## 1. Metric inventory

The platform collects nine families of Ethereum metrics on the cadences shown below. All snapshots are stored timezone‑aware (UTC) and exposed both as time‑series charts and as alert‑rule targets.

| Family | What is captured | Collection cadence | Source |
|---|---|---|---|
| Finality | Head, safe, finalized block heights; current justified epoch, finalized epoch, justified‑finalized epoch gap; time since the finalized epoch advanced | every **30 s** | Ethereum execution‑layer RPC + Beacon API |
| Time‑to‑finality (soft) | Average inter‑arrival time between newly produced blocks | every **10 min** | Execution‑layer WebSocket (`eth_subscribe newHeads`) |
| Throughput | Transactions per second, blocks per second, mean base fee + median priority fee | every **30 min** | Execution‑layer RPC (`eth_feeHistory`, `eth_getBlockByNumber`) |
| Validator decentralization | Active‑validator count, total stake (ETH), Nakamoto‑liveness coefficient, Nakamoto‑safety coefficient, HHI, Renyi entropy (α = 0, 1, 2, ∞), Shapley top three values | every **5 min** | Beacon API (`/eth/v1/beacon/states/head/validators`) |
| Operator decentralization | Top operators by share, entity‑level Nakamoto coefficients (liveness + safety), operator coverage % of active stake | every **24 h** | Rated Network API (`GET /v0/eth/operators`) |
| Governance | Confirmed EIPs in the next hardfork meta‑EIP and Last‑Call EIPs (count + per‑EIP detail) | every **6 h** | GitHub (`ethereum/EIPs`) |
| TVL | Total value locked in DeFi protocols on Ethereum (USD) | every **1 h** | DefiLlama (`/v2/chains`) |
| Token activity | Inflow, outflow, transfer / transaction count, unique active addresses, total supply for USDC, USDT0, WETH, USDE, AAVE, UNI | every **3 h** | Dune Analytics |
| Releases (optional) | Last `published_at` for the configured execution / consensus client repos | configurable | GitHub releases API |

Manual operator‑entered metrics under the `GOVERNANCE` category (e.g., delay‑on‑upgrade, slashing behaviour assessments) can be layered on top of these automated feeds in the chain detail view.

---

## 2. Finality

Ethereum's finality gadget (Casper FFG) operates on **epochs** of 32 slots (≈ 6 min 24 s). A block is *justified* when ≥ ⅔ of effective stake votes for it; it becomes *finalized* one epoch after a supermajority justifies its parent. The platform tracks both the execution‑layer block tags and the consensus‑layer epochs so the two views can be reconciled.

### Stored fields

| Field | Meaning |
|---|---|
| `head_height` | Latest block visible to the execution layer |
| `safe_height` | Block height tagged `safe` by the execution layer (justified, not yet finalized) |
| `finalized_height` | Block height tagged `finalized` (Casper‑finalized) |
| `justified_epoch`, `finalized_epoch` | Current justified and finalized epochs reported by the Beacon API |
| `justified_finalized_gap` | `justified_epoch − finalized_epoch`. In healthy operation this is **1**. |
| `time_since_finality_advance` | Seconds since `finalized_epoch` last incremented |
| `head_to_finalized_time` | `head.timestamp − finalized.timestamp` (seconds) |

### How it is collected

Each polling cycle issues three parallel `eth_getBlockByNumber` calls (`latest`, `safe`, `finalized`) against the configured execution‑layer RPC pool. A separate request to `/eth/v1/beacon/states/head/finality_checkpoints` retrieves the consensus‑layer epochs. If every execution endpoint fails the workflow raises and Temporal retries on the next 30 s tick. Beacon failures degrade gracefully — the row is still written, with `justified_epoch` / `finalized_epoch` set to the sentinel value `‑1` so the gap and advance fields are surfaced as "unknown" rather than "zero".

### How to know when something is off

| Signal | Interpretation |
|---|---|
| `justified_finalized_gap ≥ 2` | Finality is stalling. Two-epoch gap usually means the gadget went a full epoch without finalizing, often due to a network split, beacon‑client bug, or attestation gossip issue. |
| `time_since_finality_advance > ~13 min` | Finality has stopped advancing. The healthy upper bound is roughly two epoch boundaries (~12 min 48 s). |
| `head_to_finalized_time > 16 min` | The L1's hard‑finality lag is widening. Anything in roughly the 12–14 min range is normal; persistent values above 16 min indicate the consensus layer is degraded. |
| `justified_epoch == ‑1` for consecutive snapshots | The Beacon API is unreachable from every configured node — execution‑layer view is still live but the consensus interpretation is unavailable. Investigate the consensus‑client endpoints. |
| Snapshot age > 10 min in alert evaluator | The alert engine emits an `ERROR` row instead of evaluating threshold rules; treat this as a *data‑quality* incident, not a chain incident. |

### Recommended alert rules

- **THRESHOLD `>=`** on `justified_finalized_gap` with `value: 2`, severity `CRITICAL`.
- **THRESHOLD `>`** on `time_since_finality_advance` with `value: 900` (15 min), severity `CRITICAL`.
- **THRESHOLD `>`** on `head_to_finalized_time` with `value: 960` (16 min), severity `WARNING`.

---

## 3. Time‑to‑finality (soft)

A WebSocket subscription to `eth_subscribe("newHeads")` captures three consecutive block notifications and stores the mean of the two inter‑arrival gaps. This is a **soft** measure — it answers "how often does Ethereum produce a block right now?", not "how long until it cannot be reverted?".

Expected value: ≈ **12 s** (Ethereum's slot time). Values consistently > 14 s indicate a measurable share of missed slots; > 16 s for 30+ minutes is a leading indicator that proposers are offline, the network is under heavy load, or RPC endpoints are silently delaying notifications. Pair this with the finality metrics in §2 before concluding the chain itself is the problem — slow soft‑finality with healthy hard finality often points at the RPC provider rather than the chain.

Recommended alert: **STDDEV_DEVIATION** with `multiplier: 3.0`, `lookback_seconds: 21600` (6 h), `direction: above`. This catches sustained slot‑production regressions without false alarms during one‑off MEV spikes.

---

## 4. Throughput

Every 30 minutes the platform samples 10 blocks evenly spaced across the prior hour to compute network‑wide TPS (sum of transaction counts ÷ wall‑clock span) and BPS (block count over a 5 min lookback), and queries `eth_feeHistory` for the mean base fee and median priority fee.

| Field | Unit |
|---|---|
| `transactions_per_second` | tx/s (network‑wide estimate, not per slot) |
| `blocks_per_second` | blocks/s (≈ 0.083 = 1 block / 12 s for healthy mainnet) |
| `gas_price` | wei — mean base fee + P50 priority fee |

### How to know when something is off

- **BPS deviation:** healthy mainnet stays in a narrow band around 0.083 blocks/s. Drops below 0.075 for an hour mean a non‑trivial share of slots were missed.
- **TPS collapse:** Ethereum mainnet steady‑state TPS sits in the low‑teens. A drop to single digits that persists past one snapshot is significant; treat with a **RATE_OF_CHANGE** rule (`delta_pct: 25`, `window_seconds: 7200`, `direction: below`).
- **Gas‑price spike:** during fee surges, base fee can rise 10× within minutes. A **STDDEV_DEVIATION** rule with `multiplier: 4.0` and `lookback_seconds: 86400` is more robust than a fixed threshold because the band auto‑widens with normal volatility.

---

## 5. Validator decentralization

Sampled every 5 minutes from the Beacon API. The Beacon node returns every active validator's effective balance; from this the platform computes the canonical distribution metrics used in Nakamoto‑style analyses.

| Metric | What it measures |
|---|---|
| `number_of_nodes` | Count of validators in `active_ongoing` status |
| `total_amount_of_stakes` | Sum of effective balances, denominated in ETH |
| `nakamoto_liveness_coefficient` | Minimum number of validators whose combined stake exceeds the ⅓ liveness threshold |
| `nakamoto_safety_coefficient` | Minimum number of validators whose combined stake exceeds the ⅔ safety threshold |
| `hhi` | Herfindahl–Hirschman Index of stake share (0 → uniform, 1 → monopoly) |
| `renyi_entropy_alpha_0/1/2/inf` | Rényi entropy at α = 0 (richness), 1 (Shannon), 2 (collision), ∞ (worst-case concentration). Larger ⇒ more decentralized. |
| `shapley_top_value`, `shapley_second_value`, `shapley_third_value` | Shapley voting power of the top three validators (fraction in `[0, 1]`) |

### How to know when something is off

These metrics drift slowly; week‑on‑week change matters more than minute‑to‑minute change. Use **STDDEV_DEVIATION** with a long lookback (e.g., 30 days) and a tight multiplier (1.5–2.0) to surface structural shifts. Concrete red flags:

- **`nakamoto_safety_coefficient` declining** is the single most important number on the page. A move from the upper 1,000s to the high hundreds means the cost to mount a ⅔ attack is dropping.
- **HHI rising above ~0.04** is unusual for mainnet (effective number of independent stakers below ~25) and warrants investigation in the operator view (§6).
- **`total_amount_of_stakes` flat for > 24 h** while head height advances normally usually means the Beacon API is degraded — not a real stake change.

---

## 6. Operator decentralization

Validators are not the whole story: many validator keys are operated by a single entity (Lido, Coinbase, Kiln, etc.). Once per day, the platform queries the Rated Network operators endpoint and stores entity‑level Nakamoto coefficients, top‑operator stake shares, and a `coverage_pct` indicating what fraction of active stake Rated has labelled.

| Field | Meaning |
|---|---|
| `entity_nakamoto_liveness` | Number of entities whose combined stake exceeds the ⅓ liveness threshold |
| `entity_nakamoto_safety` | Number of entities whose combined stake exceeds the ⅔ safety threshold |
| `entity_count` | Total distinct operators labelled by Rated |
| `coverage_pct` | Fraction of active stake whose operator is identified (0–1) |
| `top_operators[]` | Rank, name, validator count, stake (ETH), stake share (0–1) |

### How to know when something is off

- **`entity_nakamoto_safety ≤ 6`** has historically been the single most-watched centralisation signal on Ethereum mainnet — at or below this level a coordinated 6‑entity collusion could in principle finalize a chain split.
- **A single operator's `stake_share` ≥ ⅓** is an immediate liveness concern; ≥ ⅔ is an immediate safety concern. Set `THRESHOLD` rules on the top‑operator share directly.
- **`coverage_pct` dropping** does not mean decentralization improved — it means Rated lost visibility into a chunk of the stake. Compare with the validator‑level Nakamoto numbers to disambiguate.

The Rated endpoint requires a Bearer key; if the key is missing or rate‑limited, the workflow logs and skips the cycle and the chart stays flat for that 24 h slot. Look at the freshness badge on the chain detail page before drawing conclusions from a stale snapshot.

---

## 7. Governance

Ethereum governance is off‑chain. The platform tracks two GitHub feeds that together approximate "what changes are being scheduled into mainnet":

- **Confirmed EIPs in the next hardfork meta‑EIP.** The current meta‑EIP (e.g., 7607 for the in‑progress fork) is parsed from `ethereum/EIPs`; the count of confirmed inclusions is stored. A rising count over the months leading up to a hardfork is normal; a sudden swing in either direction usually correlates with All Core Devs (ACD) calls.
- **Last‑Call EIPs.** EIPs whose frontmatter `status: Last Call` is detected via raw‑markdown fetches. Last‑Call status is a fixed‑duration review window before `Final`; an EIP in Last Call is days away from acceptance.

The platform stores the *count* per snapshot; the per‑EIP detail (number, title, status, link) is kept in the snapshot's evidence payload so the UI can render a list.

### How to know when something is off

These are slow‑moving counts; alerting on absolute values rarely makes sense. Use them as situational context: a sudden 5+ jump in confirmed‑EIPs within one snapshot window typically means a fork scope decision just happened upstream. Pair with public ACD notes before acting.

---

## 8. TVL

Hourly poll of `https://api.llama.fi/v2/chains`. The DefiLlama payload contains one entry per chain (matched on the display name `Ethereum`); the `tvl` field is stored in USD as a `Decimal` for precision.

### How to know when something is off

- **A single‑hour drop of ≥ 10 %** on a chain the size of Ethereum is rare and worth investigating — it usually corresponds either to a price crash on a dominant asset (stETH, WBTC) or to a DefiLlama re‑adapter event that retroactively excludes a protocol. Inspect the chart legend before assuming a real outflow.
- **DefiLlama outage** appears as a flat line, not a zero. The fetcher raises on HTTP failure and Temporal retries, so a true `0` row should never appear unless the upstream API legitimately returned it.

Recommended alert: **RATE_OF_CHANGE** with `delta_pct: 10`, `window_seconds: 3600`, `direction: below`, severity `WARNING`.

---

## 9. Token activity (Ethereum‑native and bridged)

Every three hours the platform runs Dune SQL against `tokens.transfers` and equivalent tables for the following tokens on Ethereum mainnet:

| Token | Stored fields | Stored as |
|---|---|---|
| USDC | inflow (USD), outflow (USD), transaction count, unique addresses, total supply | full panel |
| USDT0 | total transfer amount, inflow, outflow, transaction count, unique addresses, TVL | full panel |
| WETH | inflow, outflow, total supply | partial panel |
| USDE | total supply, transfer count, unique addresses, volume | partial panel |
| AAVE | total supply, transfer count, unique addresses, volume | partial panel |
| UNI | total supply, transfer count, unique addresses, volume | partial panel |

The lookback window is one rolling hour, shifted backward by Dune's ~3 h replication lag — so a snapshot dated 12:00 UTC reflects activity in the 08:00–09:00 UTC window.

### How to know when something is off

- **Inflow/outflow asymmetry on stablecoins (USDC, USDT0):** a window where outflow exceeds inflow by more than three days' worth of typical net‑flow is the canonical early signal of a depeg or bridge incident. Use **STDDEV_DEVIATION** on `(outflow − inflow)`.
- **Total supply jumping discontinuously:** any non‑smooth change in `*_total_supply` for a centrally‑issued token (USDC, USDT0, USDE) is either a mint/burn event or a Dune query regression. Cross‑check against the issuer's published mint/burn ledger.
- **Unique addresses ≈ 0 for one snapshot, then back to normal:** treat as a Dune indexer hiccup, not a chain event. Look at the chart context — isolated zeros are virtually always upstream issues.

---

## 10. Alert‑rule mechanics

Every metric in this document can be wrapped in one of three rule kinds. The evaluator runs every 30 seconds and uses **edge‑trigger** semantics: only state transitions (`OK → TRIGGERED`, `TRIGGERED → RECOVERED`) emit history rows and notifications.

| Rule kind | When it fires |
|---|---|
| **THRESHOLD** | The latest sample crosses a fixed boundary (`>`, `>=`, `<`, `<=`, `==`, `!=`). Best for hard limits with operational meaning, e.g., `justified_finalized_gap >= 2`. |
| **RATE_OF_CHANGE** | The latest sample differs from the sample at `now − window_seconds` by more than `delta_pct` percent. Best for "the chain changed faster than is normal" without coupling to absolute levels. |
| **STDDEV_DEVIATION** | The latest sample lies more than `multiplier × σ` from the rolling mean across `lookback_seconds`. Best for slowly‑drifting metrics whose normal range itself shifts over time. Requires at least 10 samples in the window; flat series do not fire. |

### Severity tiers

`INFO`, `WARNING`, `CRITICAL` — purely a label on the notification. The evaluator does not change behaviour by severity; use it for routing (e.g., `CRITICAL` to PagerDuty, `WARNING` to email).

### Stale‑data handling

If the most recent snapshot for an alert's target is **more than 10 minutes old**, the evaluator emits an `ERROR` history row rather than a `TRIGGERED` or `RECOVERED` transition. `ERROR` rows surface in the alert history page with the freshness gap as the message. They mean *the system could not evaluate*, not *the metric crossed a line* — investigate the collector before assuming the chain is degraded.

---

## 11. Probability‑of‑Default (when configured)

For protocol markets configured to run the scorer (Aave V3, Compound V3, etc., on Ethereum), the platform computes a per‑market PD in `[0, 1]` once an hour. The PD is multiplicative across three terms:

```
final_pd = anchors_term × control_term × assurance_term
anchors_term = 1 − ∏(1 − pd_i × weight_i)
control_term = clamp(∏(multiplier_i × weight_i), [0.75, 1.25])
assurance_term = clamp(∏(value_i × weight_i), [0.75, 1.25])
```

Anchors push PD up; controls and assurance can move it down by at most 25 % or up by at most 25 %. When a category has no entries it defaults to a neutral `1.0`. There is no platform‑wide "danger zone" cutoff; each team configures its own thresholds based on tolerance.

A practical convention: `final_pd ≥ 0.10` is high enough to warrant a review and `≥ 0.25` is high enough to warrant action, but these are policy decisions, not platform defaults.

---

## 12. Data‑source attribution

| Source | Used for | Notes |
|---|---|---|
| Configured execution‑layer RPCs | Finality, throughput, time‑to‑finality | Multi‑endpoint failover; ordered list, first success wins |
| Configured Beacon API endpoints | Finality (epochs), validator decentralization | Beacon failure degrades finality view to sentinel ‑1 values |
| Rated Network (`api.rated.network`) | Operator decentralization | Bearer key; daily cadence to respect upstream rate limits |
| GitHub (`api.github.com`, `raw.githubusercontent.com`) | Governance EIPs, releases | Unauthenticated quota 1,500/h; token quota 5,000/h |
| DefiLlama (`api.llama.fi/v2/chains`) | TVL | Match on display name `Ethereum` |
| Dune Analytics | Token activity | ~3 h replication lag; queries shift their windows accordingly |

All HTTP calls have a 30 s timeout. All workflow activities are wrapped in Temporal retry policies with exponential back‑off so an upstream blip does not lose a sample; persistent failure produces an `ERROR` row in the alert history surface so the data gap is visible.

---

*Generated from the Certora Blockchain Risk Assessment platform metric pipeline. Source‑of‑truth code paths: `src/cert_ra/metrics/*` and `src/cert_ra/api/domain/alerts/*`.*
