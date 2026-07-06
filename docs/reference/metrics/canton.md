# Canton Network (Global Synchronizer) — Risk Metrics Reference

**Audience:** Risk analysts, compliance teams, treasury and protocol operations.
**What this document covers:** Every Canton Network metric collected by the Certora Blockchain Risk Assessment platform — what it measures, how it is collected, and how to recognise when it indicates trouble.

> **Reading note.** Canton is fundamentally different from the other chains in this platform, and its metrics are translated accordingly. There are **no blocks, no slots, no mempool, and no proof‑of‑stake validator set**. The public Canton Network runs on the **Global Synchronizer**, whose infrastructure is operated by a set of **Super Validators (SVs)** that run a CometBFT consensus layer requiring **> ⅔ of SVs** to advance state and to approve governance changes. Transaction finality is **deterministic and immediate**: a transaction is final the instant its BFT‑ordered two‑phase commit completes — there is no Ethereum‑style "safe → finalized" gradient to measure. The network's economic clock is the **round**, which opens roughly every 10 minutes. Where Ethereum metrics talk about epochs and Casper finality, the Canton equivalents talk about round cadence, ledger freshness, and the SV consensus quorum margin. Because SVs vote with **equal (one‑SV‑one‑vote) power**, stake‑weighted decentralization measures (HHI, Shapley, Rényi entropy) do not apply; the governance Nakamoto coefficient does.

---

## 1. Metric inventory

The platform collects three families of Canton metrics on the cadences shown below. All snapshots are stored timezone‑aware (UTC) and exposed both as time‑series charts and as alert‑rule targets.

| Family | What is captured | Collection cadence | Source |
|---|---|---|---|
| Finality (combined) | Latest mining‑round number; seconds since the current round opened; round window length; open‑round count; ledger freshness; live SV count; BFT voting threshold; SV quorum margin | every **5 min** | Splice Scan API (`/v0/dso`, `/v0/open-and-issuing-mining-rounds`, `/v0/state/acs/snapshot-timestamp`) |
| Throughput | Amulet price (USD per Canton Coin); ledger updates per second; rounds per second | every **30 min** | Splice Scan API (`/v0/open-and-issuing-mining-rounds`, `/v2/updates`) |
| Super‑Validator decentralization | SV count; ordinary‑validator count; BFT voting threshold; governance Nakamoto safety and liveness coefficients; distinct sequencer count | every **15 min** | Splice Scan API (`/v0/dso`, `/v0/scans`, `/v0/dso-sequencers`, `/v0/admin/validator/licenses`) |

Throughput is stored in the platform's shared `throughput` table with Canton semantics mapped onto its three columns (`gas_price` = amulet price, `transactions_per_second` = updates/sec, `blocks_per_second` = rounds/sec), so the chart and alert tooling behave identically to the other chains.

Manual operator‑entered metrics under the `GOVERNANCE` category (e.g., upgrade‑authority assessments, emergency‑powers risk) can be layered on top of these automated feeds in the chain detail view, exactly as for the other chains.

Section 5 lists the metric families that the platform collects for proof‑of‑stake chains but **intentionally does not collect for Canton**, with the reason for each.

---

## 2. Finality (combined)

Canton finality is deterministic, so this family does **not** track a reversible→irreversible block gradient. Instead it captures the two things that *can* stall on a healthy‑by‑design network: whether the network is still **advancing** (round cadence and ledger freshness), and how much headroom the **BFT consensus quorum** has above its > ⅔ threshold. Both are combined into a single snapshot.

### Stored fields

| Field | Meaning |
|---|---|
| `latest_round_number` | Number of the mining round currently open (the one whose `opensAt` is most recently in the past) |
| `round_advance_seconds` | Wall‑clock seconds since that round opened. A positive "time since the network last opened a round" signal |
| `round_window_seconds` | The round's nominal open window (`targetClosesAt − opensAt`), normally ≈ 1200 s (20 min) |
| `open_round_count` | Number of rounds currently open. Rounds are staggered (≈ 20 min lifetime, ≈ 10 min cadence), so 2–3 are normally open at once |
| `ledger_freshness_seconds` | Seconds since the most recent Active Contract Set (ACS) snapshot `record_time`. ACS snapshots are produced hourly, so this oscillates in `[0, ~3600]` in healthy operation. `‑1` means the value could not be read |
| `live_sv_count` | Number of Super Validators reported in the DSO's `sv_node_states` |
| `voting_threshold` | The DSO's BFT voting threshold (> ⅔ of SVs; e.g., 9 of 13) |
| `sv_quorum_margin` | `live_sv_count − voting_threshold`. SVs that could drop before the BFT quorum can no longer be met |

### How it is collected

Each cycle queries `/v0/dso` (voting threshold + SV node states), `/v0/open-and-issuing-mining-rounds` (the staggered set of open rounds, returned as a contract‑id‑keyed map), and `/v0/state/acs/snapshot-timestamp` (most recent ACS snapshot time). The Scan endpoints are tried in configured order until one responds. If the round data is unavailable the workflow raises and Temporal retries on the next tick; if only the snapshot‑timestamp call fails, the row is still written with `ledger_freshness_seconds = ‑1` so freshness is surfaced as "unknown" rather than "zero".

### How to know when something is off

| Signal | Interpretation |
|---|---|
| `round_advance_seconds > ~1200` | No new round has opened in two nominal windows. Round minting has stalled — typically a sequencer or BFT‑ordering problem on the synchronizer. |
| `sv_quorum_margin ≤ 1` | The network is one or two SV outages away from losing its > ⅔ quorum. At `≤ 0` the quorum can no longer be met and the synchronizer cannot advance state or approve governance — a catastrophic‑tier signal. |
| `open_round_count` falling to 0–1 | Issuance/settlement is lagging — the staggered round pipeline is not being replenished. |
| `ledger_freshness_seconds > ~7200` | ACS snapshots have not advanced for two snapshot intervals — the ledger‑snapshot pipeline (or the Scan instance serving it) is degraded. Confirm against round cadence before concluding the chain itself stalled. |
| `ledger_freshness_seconds == ‑1` for consecutive snapshots | The snapshot‑timestamp endpoint is unreachable or the configured synchronizer migration id is stale. A data‑quality issue, not a chain incident. |
| `live_sv_count` dropping | Super Validators are leaving the active set. Read alongside §4. |

### Recommended alert rules

- **THRESHOLD `<=`** on `sv_quorum_margin` with `value: 1`, severity `CRITICAL`.
- **THRESHOLD `>`** on `round_advance_seconds` with `value: 1200` (20 min), severity `CRITICAL`; a `WARNING` companion at `900`.
- **THRESHOLD `>`** on `ledger_freshness_seconds` with `value: 7200` (2 h), severity `WARNING`.

---

## 3. Throughput

Canton has no gas and no blocks, so the three throughput columns carry their closest native equivalents, sampled every 30 minutes from the Scan API.

| Field | Canton meaning | Unit |
|---|---|---|
| `gas_price` | **Amulet price** — the conversion rate from the latest open round (`amuletPrice`) | USD per Canton Coin |
| `transactions_per_second` | **Updates per second** — ledger updates (transactions) counted from the bulk `/v2/updates` stream over a recent window divided by the window length | updates/s |
| `blocks_per_second` | **Rounds per second** — the cadence of the economic round, Canton's native time unit | rounds/s (≈ 0.00167 = 1 round / 10 min) |

### How it is collected

`gas_price` and `blocks_per_second` are derived from `/v0/open-and-issuing-mining-rounds`: the amulet price is read from the highest‑numbered open round, and the round cadence is measured from the spacing of consecutive rounds' `opensAt` timestamps (falling back to the nominal 600 s when only one round is visible). `transactions_per_second` is the count of updates returned by `/v2/updates` for the trailing window (default 60 s) divided by that window. The updates page size is capped at 1000 by the Scan API; if a window's update count reaches that cap the resulting rate is a **floor** (and the collector logs it). When the updates call fails, `transactions_per_second` is written as `‑1` (unknown) and the other two fields still persist.

### How to know when something is off

- **`blocks_per_second` falling below ~0.0013** (rounds opening slower than every ~13 min) for more than one snapshot indicates the round cadence is slipping — corroborate with `round_advance_seconds` in §2.
- **`transactions_per_second` collapsing** is meaningful only relative to baseline; Canton's update rate varies with application activity. Use **STDDEV_DEVIATION** with a multi‑day lookback rather than a fixed threshold. A `‑1` is a *data* gap (the `/v2/updates` call failed), not zero activity.
- **`gas_price` (amulet price) moving sharply** reflects the network's economic conversion rate, not a liveness problem. A **STDDEV_DEVIATION** rule (`multiplier: 4.0`, `lookback_seconds: 86400`) flags abnormal repricing while tolerating normal drift.

---

## 4. Super‑Validator decentralization (governance Nakamoto)

Sampled every 15 minutes. Because SVs vote with equal power, the decentralization question is **count‑based**, not stake‑weighted: how many SVs must collude to force, or to block, a > ⅔ governance decision. The platform reports the governance Nakamoto coefficients directly, alongside the sizes of the SV, validator, and sequencer sets.

### Stored fields

| Field | What it measures |
|---|---|
| `sv_count` | Distinct Super Validators known to the DSO / Scan network (`N`) |
| `validator_count` | Ordinary (non‑SV) validators with an approved licence, counted by paginating the validator‑licence list |
| `voting_threshold` | The DSO's BFT voting threshold (> ⅔ of `N`) |
| `gov_nakamoto_safety` | Minimum SVs that must collude to **block** a > ⅔ governance vote: `⌊N / 3⌋ + 1`. Higher ⇒ more decentralized |
| `gov_nakamoto_liveness` | Minimum SVs whose simultaneous outage **stalls** governance: `N − voting_threshold + 1` |
| `distinct_sequencer_count` | Number of distinct synchronizer sequencers (normally about one per SV) |

### How it is collected

`/v0/scans` provides the roster of approved SV Scans (deduplicated by SV name, falling back to the DSO's `sv_node_states` count); `/v0/dso` provides the voting threshold; `/v0/dso-sequencers` provides the sequencer set; and `/v0/admin/validator/licenses` is paginated (bounded by a configurable page cap) to count ordinary validators. The two Nakamoto coefficients are computed from `N` and the voting threshold. If the validator‑licence pagination hits its page cap the count is a floor and the collector logs it.

### How to know when something is off

- **`gov_nakamoto_safety` declining** is the single most important number here. It falls only when the SV set shrinks; a lower value means fewer colluding SVs are required to block governance, i.e., less censorship resistance in the governance process.
- **`sv_count` dropping** is the upstream cause of any safety/liveness decline — investigate which SV left the active set, and read it alongside `sv_quorum_margin` in §2 (the consensus‑side view of the same event).
- **`gov_nakamoto_liveness` falling to 1** means a single SV outage can stall governance — a concentration concern even though consensus on transactions may continue.
- **`validator_count` flat for many snapshots** while rounds keep advancing usually indicates the Scan instance serving the licence list is degraded, not that onboarding stopped.

### Recommended alert rules

- **THRESHOLD `<=`** on `gov_nakamoto_safety` at a floor your governance‑risk policy can justify (e.g., `value: 3`), severity `WARNING`.
- **STDDEV_DEVIATION** on `sv_count` with a long lookback (e.g., 30 days) and a tight multiplier (1.5–2.0) to surface structural changes in the SV set.

---

## 5. Metrics intentionally not collected for Canton

To set expectations against the proof‑of‑stake chains in this platform, the following families are **not** collected for Canton, by design:

| Family | Why not |
|---|---|
| Time‑to‑finality (soft) | Finality is deterministic and immediate on commit; there is no probabilistic block‑production interval to sample. Round cadence (§2) is the closest analog and is already captured. |
| Stake‑weighted validator decentralization (HHI, Shapley, Rényi) | SVs vote with equal power, so these measures are degenerate. The governance Nakamoto coefficient (§4) is the meaningful substitute. |
| Operator decentralization (entity labels) | Canton SVs are already named, independently‑operated institutions; there is no validator‑key‑to‑operator mapping problem to solve, and no third‑party labelling service equivalent to Rated. |
| Governance feed | Canton governance is **on‑chain** through the DSO rather than an off‑chain proposal repo. SV count and voting threshold (§4) capture the governance‑power structure directly. |
| TVL | Canton is not tracked as a chain in the DefiLlama `/v2/chains` feed used for the other chains. |
| Token activity | The platform's token‑activity panels target EVM/Solana stablecoins; Canton Coin and Canton‑hosted assets are not in that set. |

If any of these become relevant, they can be added as new collectors without changing the families above.

---

## 6. Alert‑rule mechanics

Every metric in this document can be wrapped in one of three rule kinds. The evaluator runs every 30 seconds and uses **edge‑trigger** semantics: only state transitions (`OK → TRIGGERED`, `TRIGGERED → RECOVERED`) emit history rows and notifications.

| Rule kind | When it fires |
|---|---|
| **THRESHOLD** | The latest sample crosses a fixed boundary (`>`, `>=`, `<`, `<=`, `==`, `!=`). Best for hard limits with operational meaning, e.g., `sv_quorum_margin <= 1`. |
| **RATE_OF_CHANGE** | The latest sample differs from the sample at `now − window_seconds` by more than `delta_pct` percent. Best for "the network changed faster than is normal" without coupling to absolute levels. |
| **STDDEV_DEVIATION** | The latest sample lies more than `multiplier × σ` from the rolling mean across `lookback_seconds`. Best for slowly‑drifting metrics whose normal range itself shifts. Requires at least 10 samples in the window; flat series do not fire. |

### Severity tiers

`INFO`, `WARNING`, `CRITICAL` — purely a label on the notification. The evaluator does not change behaviour by severity; use it for routing (e.g., `CRITICAL` to PagerDuty, `WARNING` to email).

### Stale‑data handling

If the most recent snapshot for an alert's target is **more than 10 minutes old**, the evaluator emits an `ERROR` history row rather than a `TRIGGERED` or `RECOVERED` transition. Because the Canton finality feed runs on a 5‑minute cadence, a single missed cycle does not trip this; two consecutive misses will. `ERROR` means *the system could not evaluate*, not *the metric crossed a line* — investigate the collector (or the Scan endpoint) before assuming the network is degraded.

---

## 7. Data‑source attribution

| Source | Used for | Notes |
|---|---|---|
| Splice **Scan API** (configured via `CERT_RA_CANTON_SCAN_URLS`) | All Canton metrics | Ordered list of Scan roots, first success wins. Each Super Validator hosts a redundant Scan; the platform can be pointed at several and reconcile, matching Canton's own trust model. |
| `/v0/dso` | Finality, decentralization | Voting threshold, SV node states, latest mining round |
| `/v0/open-and-issuing-mining-rounds` | Finality, throughput | Open rounds as a contract‑id‑keyed map: round number, `opensAt`, `targetClosesAt`, `amuletPrice` |
| `/v0/scans`, `/v0/dso-sequencers`, `/v0/admin/validator/licenses` | Decentralization | SV roster, sequencer set, paginated validator licences |
| `/v0/state/acs/snapshot-timestamp` | Finality (ledger freshness) | Requires the current synchronizer migration id; hourly ACS snapshot cadence |
| `/v2/updates` | Throughput (updates/sec) | Bulk update stream; cursor by record time + migration id; page size capped at 1000 |

> **Access note.** MainNet is permissioned: the raw per‑Super‑Validator Scan endpoints are IP‑allow‑listed and reject un‑allow‑listed callers. The platform therefore defaults to a public Scan endpoint that serves the same MainNet data, and can be repointed at allow‑listed SV Scans (queried and reconciled across several SVs) for direct, trust‑minimized reads once the deployment's egress is allow‑listed.

All HTTP calls have a 30 s timeout. All workflow activities are wrapped in Temporal retry policies with exponential back‑off so an upstream blip (including transient rate‑limit responses) does not lose a sample; persistent failure produces an `ERROR` row in the alert history surface so the data gap is visible.

---

*Generated from the Certora Blockchain Risk Assessment platform metric pipeline. Source‑of‑truth code paths: `src/cert_ra/metrics/canton/*`, `src/cert_ra/metrics/throughput/canton.py`, and `src/cert_ra/api/domain/alerts/*`.*
