# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — hybrid model

Everything below lives on `feature/hybrid-backend` and is **not yet tagged or
merged**; tagging is a pending decision. Nothing here has run on real AWS.

This release is a rebuild, not an increment. ADR-002 replaced the original
architecture — a self-managed Kubernetes cluster on EC2 with Redis — with a
thin customer-installed agent talking to a shared serverless backend, because
the infrastructure budget had to be approximately zero forever and EC2 bills by
the hour from day one.

### Added

- **Agent** (`services/agent/`) — thin client with no ML and no AWS SDK.
  Batching collector, `register` / `status` CLI, and a pluggable enforcer:
  nginx and iptables adapters auto-detect and can run *simultaneously*, rather
  than binding to one mechanism at install time as fail2ban does.
- **Backend** (`services/backend/`) — FastAPI on Lambda via Mangum. Agent,
  dashboard and admin APIs; per-tenant IsolationForest scoring; telemetry
  aggregated to one item per (tenant, IP, 5-second bucket) with a sliding
  window that survives bucket-boundary evasion.
- **Dashboard and Control Platform** — server-rendered HTML from the same
  Lambda, avoiding separate static hosting.
- **Daily retraining** via an EventBridge rule into a dedicated Lambda.
- **Model validation gate** — a new model reaches production only if it blocks
  ≤15% of validation traffic and its score spread has not widened against the
  incumbent. Ported from the superseded `ai_engine/ml/validator.py`.
- **Free-tier throttling** (PRD US-4 AC3) — telemetry ingest is refused with
  `429` + `Retry-After` at 100% of the day's share, while reads keep serving so
  agents already enforcing a block do not go open.
- **CI** (`.github/workflows/ci.yml`) — lint, full suite, 80% coverage gate and
  a dependency audit, each mirroring a quality gate the project already held
  itself to.
- **Cost regression tests** — `test_perf_budget.py` counts real DynamoDB API
  calls and fails if write cost ever starts scaling with log volume.

### Changed

- **JWT library: python-jose → PyJWT** (ADR-003). jose pins `pyasn1<0.5.0`,
  which cannot reach a patched version, and drags in `ecdsa`, whose advisory
  has no fix at all.
- Dependencies moved to patched versions across the board; `pip-audit` is clean
  for both declared requirement sets.
- `scripts/run_tests.sh` rewritten to run exactly what CI runs.
- README, architecture, MLOps design and runbook rewritten for the hybrid
  model. The previous README claimed the system was "deployed and verified
  stable on AWS"; it never was.

### Removed

- The entire superseded model: `k8s/`, the EC2/VPC/ECR Terraform modules,
  `services/ai_engine/`, `services/worker_orchestrator/`, their tests, the
  FluentBit config and ten infrastructure scripts — 119 tracked files.
- `.github/workflows/deploy.yml`, which triggered on push to `main` filtered on
  `services/**` and would have deployed the superseded architecture the moment
  this branch merged.
- Kept deliberately: the `github-oidc` and `s3-backend` Terraform modules, which
  Phase 7 will reuse unchanged (see `terraform/README.md`).

### Fixed

- `agent_auth` never hashed the incoming API key, so no genuinely registered
  agent could ever have authenticated. Every prior test passed only because it
  hand-matched the same literal on both sides.
- `MitigationState.expires_at` was hardcoded to `0`, leaving agents no TTL to
  schedule an auto-unblock from — every block was effectively permanent.
- `requirements.txt` was missing `httpx2`; the suite could not be collected on
  any clean machine.
- A DynamoDB `UpdateItem` without a condition would silently half-create a
  tenant record when suspending a non-existent tenant.

### Security

Full detail in `docs/security-report.md` — nine findings, four High, each fixed
with a failing test written first.

- `admin_auth` accepted Cognito **access tokens** and tokens minted for any app
  client in the pool: `token_use` was unchecked and audience verification was
  disabled by default. Now ID tokens only, `aud` and `iss` mandatory, RS256
  pinned, and authentication fails closed when unconfigured.
- **Suspending a tenant did nothing.** Only the agent's own status was ever
  checked. Suspension now blocks ingest, revokes every issued agent key, and
  refuses re-registration.
- **The agent trusted the backend blindly** — an unvalidated string was written
  into an nginx config and reloaded, which a newline turns into arbitrary
  directive injection on the customer's machine. Now validated at both ends.
- CSRF double-submit tokens on the cookie-authenticated UI, a full set of
  security headers, and metering that ignores unauthenticated requests so
  anonymous traffic cannot burn the free-tier quota.
