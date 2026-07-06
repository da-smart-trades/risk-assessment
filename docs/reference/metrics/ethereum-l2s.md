# Ethereum L2s — Risk Metrics Reference

**Covered chains:** Arbitrum One, Base, Optimism (OP Mainnet), Ink, Unichain.
**Audience:** Risk analysts, compliance teams, treasury and protocol operations.
**What this document covers:** Every Ethereum L2 metric collected by the Certora Blockchain Risk Assessment platform — what it measures, how it is collected, and how to recognise when it indicates trouble.

> **Reading note.** All five chains in this document are *rollups* that derive their security from Ethereum L1. The single most important property of an L2 risk metric is therefore that it is **double‑gated**: a healthy L2 reading depends both on the L2's sequencer remaining live and on Ethereum L1 finality continuing to advance. This document calls out where each metric depends on the underlying L1 so the difference between "the L2 is degraded" and "Ethereum L1 is degraded" is legible.
>
> The five chains divide into three families that this document treats explicitly:
>
> - **Arbitrum One** — Nitro rollup with a Timelock + Security Council governance surface.
> - **Base, Optimism** — OP Stack chains read via standard EVM block tags.
> - **Ink, Unichain** — OP Stack chains read via the OP Stack–specific `optimism_syncStatus` RPC.
>
> Throughout the document, "EVM L2 finality" refers to Arbitrum / Base / Optimism, and "OP Stack finality" refers to Ink / Unichain.

---

## 1. Metric inventory

| Family | Arbitrum | Base | Optimism | Ink | Unichain |
|---|:--:|:--:|:--:|:--:|:--:|
| Finality (30 s) | EVM L2 | EVM L2¹ | EVM L2 | OP Stack | OP Stack |
| Time‑to‑finality / soft finality (10 min) | — | `newFlashblocks` | — | `newHeads` | `newFlashblocks` |
| Throughput (30 min) | ✓ | ✓ | ✓ | ✓ | ✓ |
| TVL (1 h) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Token activity — USDC (3 h) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Token activity — USDT0 (3 h) | — | — | ✓ | ✓ | ✓ |
| Governance (6 h) | Discourse + Timelock + Security Council | UpgradeExecutor | — | — | — |

¹ Base is an EVM L2 in the finality pipeline, but the platform omits two of the EVM L2 fields (see §2) because Base's adapter layer reports them inconsistently.

Validator‑level decentralization is **not** collected for any L2 — the chains in this document are sequencer‑driven, and the meaningful liveness/safety knobs live in the underlying L1 (Ethereum). Use the Ethereum risk reference for those.

Manual operator metrics — including state‑validation maturity (Vitalik's Rollup Milestones), exit window, delay‑on‑upgrade, and `GOVERNANCE`‑category subjective assessments — can be added to complement the automated feeds on each chain's detail view.

---

## 2. Finality

### 2.1 EVM L2 finality (Arbitrum, Base, Optimism)

Each polling cycle issues three parallel `eth_getBlockByNumber` calls (`latest`, `safe`, `finalized`) against the configured RPC pool for the L2.

| Field | Meaning |
|---|---|
| `latest_height` | Newest block visible to the queried L2 RPC |
| `safe_height` | Block height tagged `safe` — sequencer has posted the batch but the L1 confirmation is not yet final |
| `finalized_height` | Block height tagged `finalized` — the underlying L1 batch has reached Casper finality |
| `latest_to_safe_blocks` | `latest_height − safe_height` — "how far ahead of L1 publication is the sequencer running?" |
| `safe_to_finalized_blocks` | `safe_height − finalized_height` — "how many published L2 blocks are waiting for L1 finality?" |
| `time_since_last_head` | Wall‑clock seconds since `latest.timestamp` |
| `height_correlation` | Arbitrum, Optimism only: `latest_height − finalized_height` |
| `time_to_hard_finality` | Arbitrum, Optimism only: `latest.timestamp − finalized.timestamp`, in seconds |

Base intentionally **omits** `height_correlation` and `time_to_hard_finality`. The Base adapter layer reports them inconsistently across providers, so the platform stores `None` rather than a possibly misleading value.

### 2.2 OP Stack finality (Ink, Unichain)

Ink and Unichain expose `optimism_syncStatus`, which returns canonical `unsafe_l2 / safe_l2 / finalized_l2` blocks in a single call. The platform reads exactly one endpoint per chain (no failover pool); a request failure raises and Temporal retries on the next 30 s tick.

| Field | Meaning |
|---|---|
| `unsafe_height` | Sequencer head — visible immediately, no L1 publication yet |
| `safe_height` | Block whose batch has been posted to L1 |
| `finalized_height` | Block whose batch has reached Ethereum L1 Casper finality |
| `unsafe_to_safe_blocks` | `unsafe_height − safe_height` — sequencer head vs L1 publication backlog |
| `safe_to_finalized_blocks` | `safe_height − finalized_height` — L1 publication vs L1 finality backlog |
| `time_since_last_unsafe` | Wall‑clock seconds since the sequencer's last unsafe block was produced |
| `height_correlation` | `unsafe_height − finalized_height` |
| `time_to_hard_finality` | `unsafe.timestamp − finalized.timestamp`, in seconds |

### 2.3 How to know when something is off

| Signal | Interpretation |
|---|---|
| `latest_to_safe_blocks` / `unsafe_to_safe_blocks` growing | The sequencer is producing blocks but not posting batches to L1. This is the canonical "sequencer is live but withholding" symptom — usually transient, but persistent growth is a publication‑risk concern. |
| `safe_to_finalized_blocks` growing | L1 batches are posted but Ethereum L1 finality is not advancing. Cross‑check the Ethereum metrics page: if Ethereum L1 `justified_finalized_gap ≥ 2`, this is an *Ethereum* incident, not an L2 incident. |
| `time_to_hard_finality > ~16 min` on Arbitrum / OP / Ink / Unichain | The structural minimum is roughly two Casper epoch boundaries (~13 min). Values above ~16 min that persist usually indicate L1 finality slowdown, not L2 misbehaviour. |
| `time_since_last_head` / `time_since_last_unsafe > 30 s` | The L2 sequencer (or the queried RPC) has stopped producing blocks. Expected block times: Arbitrum ~0.25 s, Base / OP / Ink ~2 s, Unichain ~1 s. Sustained gaps several multiples above the block time are real outages. |
| Snapshots stuck at the same height | The queried RPC fell behind. Compare against a public block explorer for the chain before concluding the L2 is degraded. |
| Snapshot age > 10 min in alert evaluator | The alert engine emits an `ERROR` row. Treat as a data‑quality issue first, not a chain incident. |

### 2.4 Recommended alert rules

For each L2:

- **THRESHOLD `>`** on `time_to_hard_finality` (Arbitrum / OP / Ink / Unichain) with `value: 960` (16 min), severity `WARNING`. This catches L1 finality stalls automatically.
- **THRESHOLD `>`** on `time_since_last_head` (EVM L2) or `time_since_last_unsafe` (OP Stack) with a value of `30` (30 s), severity `CRITICAL`. This catches sequencer outages.
- **STDDEV_DEVIATION** on `safe_to_finalized_blocks` with `multiplier: 3.0` and `lookback_seconds: 21600` (6 h), `direction: above`. This catches gradual finality slowdowns whose absolute value sits below the hard threshold.

---

## 3. Time‑to‑finality (soft)

For chains in this pipeline, a WebSocket subscription captures three consecutive block notifications and stores the mean of the two inter‑arrival gaps. This is a measure of *block production rhythm*, not irreversibility.

| Chain | Subscription | Expected mean (s) |
|---|---|---:|
| Ink | `eth_subscribe("newHeads")` | ≈ 1.0 |
| Base | `eth_subscribe("newFlashblocks")` | ≈ 0.2 |
| Unichain | `eth_subscribe("newFlashblocks")` | ≈ 0.25 |
| Arbitrum, Optimism | not collected | — |

Base and Unichain use **flashblocks** — pre‑confirmation chunks emitted between proper blocks. The "soft finality" they expose is therefore a flashblock cadence, not a block cadence; do not compare it directly to other L2s.

### How to know when something is off

These metrics are noisy by nature. Use **STDDEV_DEVIATION** with `multiplier: 3.0`, `lookback_seconds: 21600`, `direction: above` rather than a fixed threshold. A sustained doubling of the mean inter‑arrival is significant; a single spike usually indicates either a flashblock provider blip or a momentary load surge.

---

## 4. Throughput

Every 30 minutes the platform samples 10 blocks evenly spaced across the prior hour on each L2 to compute TPS and BPS, and queries `eth_feeHistory` for the mean base fee and median priority fee.

| Field | Unit |
|---|---|
| `transactions_per_second` | tx/s (L2 only — does not include L1 batch costs) |
| `blocks_per_second` | blocks/s |
| `gas_price` | wei — mean base fee + P50 priority fee |

### Expected `blocks_per_second` baselines

| Chain | Expected BPS |
|---|---:|
| Arbitrum One | ≈ 4 |
| Base | ≈ 0.5 |
| Optimism | ≈ 0.5 |
| Ink | ≈ 0.5 |
| Unichain | ≈ 1.0 |

### How to know when something is off

- **`blocks_per_second` falling below ~80 % of baseline** for an hour is meaningful — the sequencer is either skipping or producing partial batches.
- **`transactions_per_second` collapsing** is meaningful only relative to baseline because L2 activity is uneven. Use **STDDEV_DEVIATION** with at least a 7‑day lookback.
- **`gas_price` spike of 10× or more** that persists usually correlates with a high‑traffic event on the L2 (e.g., a token launch). For L2s with a flashblock structure (Base, Unichain), spikes are even more common; pair with TPS to distinguish "many small txs" from "few expensive txs".

---

## 5. TVL

Hourly poll of `https://api.llama.fi/v2/chains`. Display names matched:

- `Arbitrum` → ARBITRUM
- `Base` → BASE
- `Optimism` and `OP Mainnet` → OPTIMISM (both names accepted)
- `Ink` → INK
- `Unichain` → UNICHAIN

Stored in USD as a `Decimal` for precision.

### How to know when something is off

- **A single‑hour drop of ≥ 10 %** on any of these L2s warrants investigation. For Arbitrum and Base, the absolute TVLs are large enough that 10 % drops are rare. For Ink and Unichain — newer chains with smaller, more concentrated DeFi — 10 % moves are more frequent and usually correspond to a single protocol being added or removed by DefiLlama.
- **DefiLlama outage** appears as a flat line, not a zero.

Recommended alert: **RATE_OF_CHANGE** with `delta_pct: 10`, `window_seconds: 3600`, `direction: below`, severity `WARNING` (Arbitrum, Base); `delta_pct: 20` for Ink, Unichain.

---

## 6. Token activity

Every three hours Dune SQL queries report inflow (USD), outflow (USD), unique active addresses, transaction count, and total supply for the tokens listed below, on a rolling one‑hour window shifted backward by Dune's ~3 h replication lag.

| Chain | USDC | USDT0 |
|---|:--:|:--:|
| Arbitrum | ✓ | — |
| Base | ✓ | — |
| Optimism | ✓ | ✓ |
| Ink | ✓ | ✓ |
| Unichain | ✓ | ✓ |

### How to know when something is off

- **Outflow exceeding inflow by more than three days' worth of typical net flow** is the canonical leading signal of a bridge incident or a major redemption event. Use **STDDEV_DEVIATION** on the `(outflow − inflow)` series with a multi‑day lookback rather than a fixed dollar threshold. This is especially actionable on L2s, where stablecoin movement is often the first indicator of users exiting the chain.
- **Total supply jumping discontinuously** on a centrally‑issued token is either a mint/burn event or a Dune query regression. USDT0 in particular tends to mint and burn in large round‑number tranches on its origin chain that propagate over the LayerZero bridge; cross‑check against the issuer's published ledger.
- **Single‑snapshot drops to zero** are virtually always Dune indexer hiccups.

---

## 7. Governance

Governance is collected only for Arbitrum and Base. Each entry below is a 6‑hour count of events; the platform stores the count and keeps per‑event details in the snapshot's evidence payload.

### 7.1 Arbitrum

| Feed | What is counted | How |
|---|---|---|
| `proposals` | Topics in the Arbitrum DAO proposals forum (last 6 h slice) | GET `https://forum.arbitrum.foundation/c/proposals/7.json` (Discourse JSON) |
| `execution` | `CallScheduled` and `CallExecuted` events on the Arbitrum Timelock at `0x34d45e99…F98f0` | `eth_getLogs` over the last ~86 400 blocks (~6 h at 0.25 s / block) |
| `emergency` | All events on the Security Council UpgradeExecutor at `0xCF575722…40A827` | `eth_getLogs` over the last ~86 400 blocks |

### 7.2 Base

| Feed | What is counted | How |
|---|---|---|
| `execution` | All events on the Base UpgradeExecutor at `0x14536667…46E056` | `eth_getLogs` over the last ~10 800 blocks (~6 h at 2 s / block) |

### 7.3 How to know when something is off

- **`emergency` count > 0 on Arbitrum** is by definition a notable event — the Security Council UpgradeExecutor is reserved for time‑sensitive interventions, and any activity warrants reading the on‑chain calldata and the DAO forum context. Set a **THRESHOLD `>`** rule on `arb_governance_emergency` with `value: 0`, severity `CRITICAL`.
- **`execution` count rising sharply** on either chain usually corresponds to a scheduled upgrade landing on‑chain. Pair with the public release notes for the chain before drawing conclusions.
- **`proposals` count swing** without a corresponding forum announcement is rare; the Discourse feed lags by minutes at most.

Optimism, Ink, and Unichain do not have automated governance feeds — their governance happens off‑chain via foundation announcements that the platform does not currently scrape. Manual operator metrics under `GOVERNANCE` (e.g., delay‑on‑upgrade, upgrade transparency) are the right place to record subjective assessments for those chains.

---

## 8. Alert‑rule mechanics

Every metric in this document can be wrapped in one of three rule kinds. The evaluator runs every 30 seconds with **edge‑trigger** semantics: only state transitions (`OK → TRIGGERED`, `TRIGGERED → RECOVERED`) emit history rows and notifications.

| Rule kind | When it fires |
|---|---|
| **THRESHOLD** | The latest sample crosses a fixed boundary (`>`, `>=`, `<`, `<=`, `==`, `!=`). Best for hard limits with operational meaning, e.g., `time_to_hard_finality > 960`. |
| **RATE_OF_CHANGE** | The latest sample differs from the sample at `now − window_seconds` by more than `delta_pct` percent. Best for "the chain changed faster than is normal". |
| **STDDEV_DEVIATION** | The latest sample lies more than `multiplier × σ` from the rolling mean across `lookback_seconds`. Best for slowly‑drifting metrics whose normal range itself shifts. Requires ≥ 10 samples; flat series do not fire. |

### Severity tiers

`INFO`, `WARNING`, `CRITICAL` — a label on the notification. The evaluator does not change behaviour by severity; use it for routing.

### Stale‑data handling

If the most recent snapshot is more than **10 minutes old**, the evaluator emits an `ERROR` history row rather than a transition. `ERROR` means *the system could not evaluate*, not *the metric crossed a line*. On an L2 this is especially important: a stale row can reflect either an L2 sequencer outage, an L1 finality issue (for `finalized`‑tagged calls), or a degraded RPC provider. Investigate the collector layer before concluding the chain itself is at fault.

---

## 9. Probability‑of‑Default (when configured)

For protocol markets deployed on these L2s and configured to run the scorer (Aave V3 on Arbitrum, Base, and Optimism; other protocols per platform configuration), the platform computes a per‑market PD in `[0, 1]` once an hour. The PD is multiplicative across three terms:

```
final_pd = anchors_term × control_term × assurance_term
anchors_term = 1 − ∏(1 − pd_i × weight_i)
control_term = clamp(∏(multiplier_i × weight_i), [0.75, 1.25])
assurance_term = clamp(∏(value_i × weight_i), [0.75, 1.25])
```

Anchors push PD up; controls and assurance can move it down by at most 25 % or up by at most 25 %. When a category has no entries it defaults to a neutral `1.0`. There is no platform‑wide "danger zone" cutoff; each team configures its own thresholds based on tolerance.

A practical convention: `final_pd ≥ 0.10` is high enough to warrant a review and `≥ 0.25` is high enough to warrant action, but these are policy decisions, not platform defaults.

L2‑specific note: market PDs computed on L2s implicitly inherit the underlying L1's finality risk. Treat a market PD on Arbitrum or Base as conditional on Ethereum L1 finality being healthy; if Ethereum L1 is in a degraded state, the effective PD is materially higher than the displayed number.

---

## 10. Data‑source attribution

| Source | Used for | Notes |
|---|---|---|
| Configured Arbitrum RPCs | Arbitrum finality, throughput, governance (Timelock + UpgradeExecutor logs) | Multi‑endpoint failover |
| Configured Base RPCs | Base finality, throughput, time‑to‑finality, governance (UpgradeExecutor logs) | Multi‑endpoint failover for HTTP; single endpoint for WebSocket |
| Configured Optimism RPCs | Optimism finality, throughput | Multi‑endpoint failover |
| Configured Ink RPC (single) | Ink finality (`optimism_syncStatus`), throughput, time‑to‑finality | Single endpoint; failure raises and retries |
| Configured Unichain RPC (single) | Unichain finality (`optimism_syncStatus`), throughput, time‑to‑finality | Single endpoint; failure raises and retries |
| Arbitrum DAO Discourse forum | Arbitrum `proposals` count | `forum.arbitrum.foundation` JSON |
| DefiLlama (`api.llama.fi/v2/chains`) | TVL | One display name per chain |
| Dune Analytics | Token activity, Solana throughput (cross‑reference) | ~3 h replication lag; queries shift their windows accordingly |

All HTTP calls have a 30 s timeout. All workflow activities are wrapped in Temporal retry policies; persistent failure surfaces as `ERROR` rows in the alert history view so the data gap is visible to teams using the metric.

---

## 11. Quick‑reference per chain

### Arbitrum One
- **Finality:** standard EVM L2 (latest / safe / finalized) — full field set including `height_correlation` and `time_to_hard_finality`.
- **Sequencer block time:** ~0.25 s.
- **Governance:** richest L2 coverage — DAO proposals + Timelock execution + Security Council emergency.
- **First place to look on incident:** `time_since_last_head` (sequencer), `safe_to_finalized_blocks` (L1 anchor).

### Base
- **Finality:** standard EVM L2 minus `height_correlation` and `time_to_hard_finality` (adapter‑layer inconsistency).
- **Sequencer block time:** ~2 s, with `newFlashblocks` pre‑confirmation chunks every ~200 ms.
- **Governance:** UpgradeExecutor log count only.
- **First place to look on incident:** `time_since_last_head`, then the soft‑finality WebSocket cadence.

### Optimism (OP Mainnet)
- **Finality:** standard EVM L2 — full field set.
- **Sequencer block time:** ~2 s.
- **Governance:** not automated — use manual `GOVERNANCE` metrics.
- **First place to look on incident:** `time_since_last_head`, then `safe_to_finalized_blocks`.

### Ink
- **Finality:** OP Stack `optimism_syncStatus` — unsafe / safe / finalized in a single call.
- **Sequencer block time:** ~1 s, no flashblocks.
- **Governance:** not automated — use manual `GOVERNANCE` metrics.
- **First place to look on incident:** `time_since_last_unsafe`, then `safe_to_finalized_blocks`.

### Unichain
- **Finality:** OP Stack `optimism_syncStatus`.
- **Sequencer block time:** ~1 s, with `newFlashblocks` pre‑confirmation chunks.
- **Governance:** not automated — use manual `GOVERNANCE` metrics.
- **First place to look on incident:** `time_since_last_unsafe`, then the soft‑finality WebSocket cadence.

---

*Generated from the Certora Blockchain Risk Assessment platform metric pipeline. Source‑of‑truth code paths: `src/cert_ra/metrics/*` and `src/cert_ra/api/domain/alerts/*`.*
