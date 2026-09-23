# ADR-006: Anomaly tiers measured in standard deviations, not absolute score

- **Date:** 2026-09-23
- **Status:** accepted
- **Amends:** the tiering introduced in Phase 3 (`TIER1_THRESHOLD = -0.1`,
  `TIER2_THRESHOLD = -0.3`), and the description of it in
  [mlops-design.md](../mlops-design.md).

## Context

The shipped design scored each feature vector with a per-tenant
IsolationForest and compared `decision_function` against two fixed constants.
That comparison is not well defined, and production showed it.

`decision_function` is `score_samples - offset_`, and scikit-learn sets
`offset_` from the `contamination` parameter at the **training set's own 1%
quantile**. The zero point therefore moves with each model. A score of -0.09
means "slightly more unusual than the most unusual 1% of what this tenant
looked like the night this model was trained" - not a fixed quantity.

Four models, one tenant (`acme-demo`), the same 150-request brute-force
attack, all measured on the live deployment:

| Model | Training data | Attacker score | Outcome |
|---|---|---|---|
| `v20260921034701` | 182 samples | **-0.204** | rate limited |
| `v20260921052419` | 187 samples | **-0.105** | rate limited, by 0.005 |
| `v20260921180054` | 192 samples, **poisoned** | — | no decision at all |
| `v20260923021938` | 177 clean samples | **-0.092** | **not detected** |

The last row is the finding. A model trained on clean data, on traffic of the
same shape as the first, **missed the same attack by 0.008**. Nothing was
wrong with that model; the threshold simply has no stable relationship to it.

### What was measured before changing anything

20 independent baselines, 180 training buckets and **300 held-out** normal
buckets each, scored against the same brute force and against a milder abuser
(40 requests, half errors):

| Threshold | brute force | mild abuse | false positives |
|---|---|---|---|
| `raw < -0.05` | 20/20 | 17/20 | 0.40% |
| `raw < -0.08` | 18/20 | 5/20 | 0.08% |
| **`raw < -0.10`** (shipped) | **14/20** | **2/20** | **0.00%** |
| `raw < -0.12` | 2/20 | 0/20 | 0.00% |
| `raw < -0.15` | **0/20** | 0/20 | 0.00% |
| `z < -3.5` | 20/20 | 20/20 | 0.82% |
| **`z < -4.0`** | **20/20** | **17/20** | **0.27%** |
| `z < -4.5` | 19/20 | 5/20 | 0.07% |
| **`z < -5.0`** | 11/20 | 0/20 | **0.00%** |

Two things fall out of that table.

**The shipped tier-2 threshold could never fire.** `raw < -0.15` caught
nothing across 20 baselines; on a `contamination=0.01` model the score lives
roughly in `[-0.2, +0.25]`. The hard-block path - an entire documented tier of
the product, with its own TTL and its own enforcement adapter behaviour - had
never once been reachable.

**A relative threshold detects strictly better at comparable cost.**
`z < -4.0` catches every brute force and 17 of 20 mild abusers for 0.27% false
positives; the nearest absolute equivalent, `raw < -0.05`, catches the same
and costs 0.40%.

The stability argument is the stronger one. Across those baselines the
attacker's raw score ranged -0.142 to -0.068 - a spread of 74% of the
threshold's own magnitude - while its z ranged -5.66 to -4.21, a spread of
36%. A raw threshold tuned on one tenant's traffic is simply wrong for
another's; a z threshold is not.

## Decision

Tier on `z = (score - score_mean) / score_std`, where the two statistics are
the model's own training-score distribution:

- `z < -4.0` → Tier 1, rate limit
- `z < -5.0` → Tier 2, hard block

`score_mean` and `score_std` have been stored in `ModelMetadata` since Phase 3
and were already surfaced by `/dashboard/v1/model/status`; they were simply
never used for anything. They now travel with the model in `ModelManager`'s
cache, so tiering costs no extra read.

**Fallback.** If `score_std <= 0` - every training bucket scored identically,
possible for a tenant whose traffic has one shape - the absolute thresholds
stand in. Dividing by zero on the request path would be a crash rather than a
detection.

## Consequences

**Detection improves and becomes tenant-independent.** The attack production
missed at -0.092 sits 5.26 standard deviations below that model's own mean,
and is now caught. The same attack against the 2026-09-21 model (-0.204,
5.9 deviations) is tiered identically - which is the whole point.

**False positives rise from 0.00% to 0.27% of normal buckets.** That is the
real price, and it is paid in Tier 1: a 300-second rate limit, not a block.
Tier 2, which does block for an hour, measured 0.00% on held-out normal
traffic at `z < -5.0`. The whitelist remains the operator's instrument for a
persistent false positive.

**The hard-block tier becomes reachable for the first time.** Roughly half the
brute-force cases cross `z < -5.0`. That path has never run in production and
its enforcement (nginx `deny` plus an iptables DROP on the customer's own
machine) is therefore the least exercised code in the product.

**A second read disappeared.** `ModelManager.load` called
`registry.model_exists()` and then `registry.load_model()`, and `model_exists`
did an unprojected `GetItem` - so every cold start fetched the ~238KB model
item **twice**, about 120 RCU against an account-wide budget of 25 RCU/second.
It is now one read that returns the blob and the statistics together, and
`model_exists` asks only for the key.

**These numbers describe synthetic normal traffic.** The baselines were
generated to match the shape production showed (3% errors, 8% POSTs, 3-9
requests per bucket). No real customer traffic has ever run through this
system, so the false-positive figure is an estimate on modelled data, not a
measurement on a live tenant. It should be re-measured against the first real
tenant's traffic before anyone treats 0.27% as a fact.
