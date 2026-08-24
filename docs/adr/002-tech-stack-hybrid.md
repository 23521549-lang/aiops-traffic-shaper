# ADR-002: Tech stack for the free hybrid model (AWS Always-Free only)

- **Date:** 2026-08-21
- **Status:** accepted

## Context

`docs/PRD.md` (retro-PRD, Support-mode Phase 1, 2026-08-21) records a pivot:
the product is no longer "one organization protects its own AWS bill" — it's
a **free product** made of a lightweight agent the end user installs in
their own system, plus a **centrally-hosted backend** the publisher
(developer) runs and pays for. The publisher's hard constraint, confirmed
directly: **infra cost target is 0đ, absolutely, forever** — not "free for
the first 12 months." The developer also explicitly wants to stay on AWS.

This rules out the old stack's core pieces: self-managed Kubernetes on EC2
(ADR-001) has an hourly EC2 cost from day one; EC2/RDS/S3/EFS/API Gateway
"Free Tier" on AWS is time-limited to 12 months, not forever.

## Options considered

1. **Oracle Cloud Always Free ARM VM + Docker Compose** — genuinely free
   forever, generous specs (up to 4 OCPU / 24GB RAM), lets the existing
   FastAPI/Redis/sklearn stack run almost unchanged. Rejected: developer
   wants to stay on AWS; also carries the risk of Oracle reclaiming
   "idle" Always Free resources.
2. **Serverless on GCP (Cloud Run + Upstash Redis)** — scale-to-zero,
   resilient, forever-free tier. Rejected: developer wants to stay on AWS;
   adds a second/third cloud vendor for no benefit once AWS's own
   always-free serverless services cover the same need.
3. **AWS EC2/RDS Free Tier (closest to the old stack)** — fastest to ship,
   reuses Terraform. Rejected: it is a **12-month** free tier, not forever —
   directly violates the confirmed "0đ forever" constraint.
4. **AWS Always-Free serverless: Lambda + DynamoDB (chosen)** — both are
   Always Free with no 12-month cutoff (Lambda: 1M requests + 400,000
   GB-seconds/month; DynamoDB: 25GB storage + 25 RCU/WCU), and the
   developer confirmed this direction directly ("dùng lambda thì sao, aws
   có những service free mà").

## Decision

| Layer | Choice | Why |
|---|---|---|
| Compute (API + ML inference) | AWS Lambda, Python 3.12, FastAPI via Mangum adapter | Always Free forever; reuses existing FastAPI route/schema code with minimal adapter change |
| HTTPS ingress | **Lambda Function URLs**, not API Gateway | API Gateway is 12-month Free Tier only; Function URLs ride on Lambda's own always-free quota at no extra cost |
| State: tenants, agents, telemetry, mitigation state, whitelist, usage counters | DynamoDB | Always Free forever (25GB + 25 RCU/WCU); replaces Redis (which needed a persistent server) |
| ML model storage | DynamoDB item, `Binary` attribute, gzip-compressed joblib dump | Verified by measurement (see below) — no S3/EFS needed, both of which are 12-month-only |
| Scheduled retraining | EventBridge scheduled rule → Lambda | No separate charge for a basic scheduled rule invoking Lambda |
| Auth (agent registration, dashboard login, control-platform login) | Amazon Cognito | Always Free forever, 50,000 MAUs |
| Dashboard + Control Platform UI | Server-rendered HTML/JS returned directly by a Lambda Function URL | Avoids S3 static hosting (12-month-only); no separate hosting component needed |
| Agent (installed on the end user's own system) | New component, thin telemetry forwarder | Runs on the *tenant's* infra, not the publisher's — out of scope for the 0đ constraint (PRD) |

### Model-size finding (measured, not assumed)

The existing `ai_engine/ml/training.py` trains `IsolationForest(n_estimators=100, ...)`
on 7 features. Measured with the project's actual venv
(`aiops-venv`, scikit-learn/joblib matching `requirements.txt`):

| n_estimators | raw joblib | gzip |
|---|---|---|
| 100 (current) | 1,869,768 B | 473,849 B |
| 50 | 934,232 B | 237,994 B |
| 30 | 559,384 B | 143,588 B |

DynamoDB's per-item limit is 400KB. **100 estimators does not fit even
gzip-compressed; 50 does, with margin for metadata attributes in the same
item.** Decision: retrain uses `n_estimators=50` for the hybrid backend
(the old model file stays untouched for reference/superseded runs). This is
a measured engineering constraint, not a stylistic choice — Phase 3 must not
silently raise `n_estimators` back up without re-checking item size.

### Multi-tenancy for the model

One IsolationForest **per tenant**, keyed by `tenant_id` in DynamoDB — not
one shared global model. This matches the original design's intent (each
org trains on its own 24h shadow-mode baseline) and keeps a bad/attacked
tenant's traffic from skewing another tenant's detection. Storage cost of
many small per-tenant models is trivial against the 25GB free allotment
(a few hundred tenants at ~240KB each is still <100MB).

### Reuse assessment of existing code (`services/ai_engine`, `services/worker_orchestrator`)

| Module | Verdict | Why |
|---|---|---|
| `ai_engine/ml/feature_engineering.py`, `model.py`, `validator.py`, `monitoring.py` | Reuse, adapt | Pure Python/sklearn logic, not infra-coupled; needs `tenant_id` threaded through |
| `ai_engine/ml/training.py` | Reuse, adapt | Same, plus `n_estimators=50` change above and a per-tenant loop invoked by the EventBridge schedule |
| `ai_engine/ml/registry.py` | Rewrite | Uses local filesystem (`/app/models`, PVC-backed) — incompatible with stateless Lambda; replace with the DynamoDB binary-item storage above |
| `ai_engine/core/redis_client.py`, `worker_orchestrator/core/redis_client.py` | Rewrite | Redis assumed a persistent server; replace with a DynamoDB client module |
| `ai_engine/api/routes/*.py` | Reuse, adapt | FastAPI routes/schemas are framework-level, not infra-coupled; swap the Redis dependency for the new DynamoDB one |
| `worker_orchestrator/orchestrator/configmap_patcher.py`, `nginx_reloader.py` | **Discard** | Tied to the abandoned K8s ConfigMap + nginx-reload cluster; the *concept* (dynamic local rate-limit enforcement) moves into the new Agent component instead, which runs on the tenant's own system |
| `worker_orchestrator/orchestrator/cleanup.py`, `metrics.py`, `api/routes/mitigate.py`, `blocklist.py` | Reuse, adapt | Logic is generic (TTL cleanup, metrics, decision API); storage swap only |
| Terraform/K8s (`terraform/`, `k8s/`) | Discard | Confirmed abandoned in `docs/PRD.md` — doesn't fit the 0đ target |

### Agent design (new component, not present in old code)

Kept intentionally thin per PRD: the agent captures minimal per-request
metadata near the tenant's own service (source IP, timestamp, path, method,
status code), batches and POSTs it to the backend's Function URL, and
enforces whatever mitigation decision the backend returns (rate-limit or
block an IP locally). All feature computation and ML inference stay
centralized in the Lambda backend, reusing `feature_engineering.py`
unchanged — this keeps the agent free of the sklearn dependency entirely,
so it stays genuinely lightweight and easy to distribute.

**Local enforcement mechanism (resolved in Stage 8, not guessed here):**
a pluggable adapter architecture, not one fixed mechanism — inspired by
CrowdSec's "bouncer" model (the enforcement-side successor to fail2ban's
single-hook design) but going further: multiple adapters (nginx, iptables)
can be auto-detected and run simultaneously on one agent, rather than
picking exactly one mechanism at install time. See `docs/PLAN.md` Stage 8
for the full design and the real bugs (a hardcoded `expires_at=0` and an
`agent_auth` that never actually hashed the incoming key) found while
building it.

### UI decision (explicit, not silent)

The product has two human-facing UIs — Dashboard (end user, tenant-scoped)
and Control Platform (publisher, cross-tenant) — both **build**, both served
as server-rendered HTML/JS directly from Lambda Function URLs. No separate
frontend framework/build pipeline decided yet; kept as a `docs/PLAN.md`
open item for Phase 3 rather than guessed here.

**Resolved in Stage 8 (backend routing) / Stage 9 (the pages
themselves):** Jinja2 templates + a small custom (~70 line) attribute-
driven JS helper, not the real htmx library — this offline environment
can't fetch/verify the actual htmx.js, and faking a well-known third-party
library from memory risked shipping something that silently isn't it.
Building this surfaced a real gap: `dashboard_auth`/`admin_auth` (Stage 6)
only read a Bearer header, which a plain HTML page navigation can never
attach — fixed with a cookie fallback into the same verification path.
See `docs/PLAN.md` Stage 9 for the full design.

## Hardening pass (2026-08-21, same session)

Before this ADR + `docs/PLAN.md` were presented for approval, the plan was
reviewed critically against its own 0đ constraint at the developer's
request. Four real weaknesses were found and **fixed in `docs/PLAN.md`
directly**, not just noted:

1. DynamoDB tables were specced with `PAY_PER_REQUEST` (on-demand) billing
   — which has **no Always-Free allowance at all** and bills from the
   first request, directly contradicting this ADR's own premise. Fixed:
   `PROVISIONED` mode, explicit ≤25 RCU/25 WCU account-wide budget
   (`docs/schema.md`, `docs/PLAN.md` Stage 1).
2. The telemetry-aggregation design (Stage 2) initially wrote to DynamoDB
   once per raw log line and stored unbounded `uris`/`user_agents` lists —
   both scaled cost with traffic volume, worst exactly during a real
   attack. Fixed: in-memory aggregation per Lambda invocation, one write
   per unique IP per batch, fixed-size numeric fields only.
3. The telemetry handler would have read the full ~238KB model item from
   DynamoDB on every request (≈60 RCU — over the entire account budget in
   one call). Fixed: `ModelManager` caches the deserialized model at class
   level across warm Lambda invocations; only a cold start hits DynamoDB.
4. A single fixed time bucket let an attacker split volume across the
   bucket boundary to stay under threshold in each half. Fixed: a
   two-bucket weighted sliding-window counter (current bucket + weighted
   previous bucket) on the read side.

One risk was found and **documented as residual, not solved**: dropping
API Gateway (for cost) also drops its built-in request throttling, so the
public Lambda Function URL has no AWS-native rate limit against anonymous
abuse. No Always-Free AWS service closes this gap (CloudFront/WAF are not
Always-Free). Accepted as bounded — Lambda's per-request cost beyond the
free tier is fractions of a cent, not an unbounded bill — with Stage 5's
usage-ceiling warning as the early-detection mechanism. Revisiting this
trade-off (e.g., accepting a few dollars/month for WAF) needs a fresh
conversation with the developer, not a silent change.

## Consequences

- Every piece of runtime state (Redis today) must be re-modeled as DynamoDB
  items before any hybrid-model code can run — this is the single largest
  Phase 3 body of work, not a drop-in swap.
- Lambda's 15-minute max execution time caps how many tenants' models can be
  retrained in one invocation; if the tenant count grows, the daily retrain
  job needs to fan out (e.g., one Lambda invocation per tenant via
  EventBridge or Step Functions) instead of looping serially — flagged in
  `docs/PLAN.md`, not solved here.
- Losing Redis's sorted-set sliding windows (documented in the old
  `docs/architecture.md` as a deliberate simplicity win) means the sliding-
  window telemetry logic must be redesigned on top of DynamoDB, which has
  different cost/latency characteristics (no free range-query sorted sets).
- CloudWatch Logs' Always Free allotment (5GB ingestion/month) is smaller
  than what the old Prometheus/Grafana setup assumed — verbose logging
  needs to be deliberately budgeted, not left at the old defaults.
- Committing to "AWS-only, Lambda + DynamoDB" means no more Kubernetes,
  Terraform, Redis, or self-managed nginx in the target architecture —
  `docs/runbook.md` and `docs/mlops-design.md` (written for the old
  cluster) become historical reference, not operational truth, until/unless
  a future ADR revisits this.
