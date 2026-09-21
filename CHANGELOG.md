# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] — 2026-09-21 — deployed

**The system runs on AWS.** Account `375916766707`, `ap-southeast-1`. The
post-deploy smoke test passes 5/5 and a backup restore has been performed
against the live DynamoDB tables. No real traffic has been served yet: no
tenant registered, no telemetry scored in production, no nightly retrain on
live data.

### Added

- **CloudFront + Origin Access Control as the public entrypoint** (ADR-005).
  The Lambda function URL is now `AWS_IAM` and reachable only through the
  distribution. Inside CloudFront's perpetual Always-Free tier (1 TB,
  10M requests/month), and AWS Shield Standard comes with it — the first real
  answer to the "no edge protection" residual risk ADR-002 had to accept.
- **Remote Terraform state** in S3 with native lock files, plus
  `terraform/bootstrap/` to create the bucket. Without it the deploy workflow
  would have started every run with empty state and tried to rebuild the whole
  stack.
- `x-amz-content-sha256` on every request that carries a body — in the agent,
  the dashboard JS, and the smoke test. OAC signs the request but not the body.

### Fixed — all three found by deploying, none findable any other way

- **A Lambda function URL behind CloudFront needs two IAM statements**, not
  one: `InvokeFunctionUrl` *and* `InvokeFunction`. Granting only the first
  produces a 403 indistinguishable from an account-level block, and cost an
  hour of confidently wrong diagnosis recorded in ADR-005.
- **`/ready` reported `dynamodb: false` on a healthy stack** — the API role was
  never granted `dynamodb:DescribeTable`. The probe was right about its own
  permissions and misleading about everything else.
- **The GitHub OIDC provider is an account-level singleton** already owned by
  another project in the same account. The module now references it instead of
  creating it; importing it would have started a fight between two Terraform
  states that neither apply wins.
- `scripts/backup-tables.sh` could not run on Windows: `python3` there is a
  Microsoft Store stub that exits non-zero, and Windows Python cannot resolve
  MSYS `/c/Users/...` paths.

### Changed

- The login form submits through `fetch` rather than as a plain HTML form post.
  A browser-built form cannot carry the body hash CloudFront requires.
- `docs/deployment.md` first-deployment section rewritten as a runnable
  checklist; README, architecture, runbook and onboarding now separate
  *deployed* from *serving real traffic*.

## [0.1.1] — 2026-09-20 — infrastructure written

Everything needed to deploy, written and validated, none of it applied. Kept as
a separate entry because it is the state the project stood in for a day, and
because the Phase 7 gate failed 2/8 against it twice - the two failing rows
being exactly the two that need a real deployment.

### Added

- **Terraform root configuration** — all 7 DynamoDB tables (provisioned,
  14 RCU / 20 WCU of the 25/25 Always-Free pool), both Lambda functions, the
  Function URL, the Cognito pool whose immutable `custom:tenant_id` the whole
  isolation story rests on, the nightly EventBridge rule, two log groups at
  14-day retention, and least-privilege IAM.
- **`.github/workflows/deploy.yml`** — manual dispatch only, behind a GitHub
  `production` environment approval, authenticating through OIDC with no
  static AWS keys. It builds the package, plans, applies on request, and runs
  the smoke test.
- **Three CloudWatch alarms → SNS** (`terraform/alarms.tf`): API errors,
  retrain failure, retrain approaching Lambda's 15-minute ceiling. Within the
  Always-Free allowance of 10 alarms and 1,000 emails.
- **`GET /ready`** — readiness, as distinct from liveness. Describes a DynamoDB
  table (a control-plane call, so it consumes no read capacity) and verifies
  both Cognito values are set. A deployment missing either authenticates nobody
  while `/health` still answers 200; that now reports as `503 not_ready` with a
  per-check body instead of looking healthy. `scripts/smoke-test.sh` checks it.
- **`scripts/backup-tables.sh`** — export/restore for `Tenants`, `Agents` and
  `Whitelist`, the three tables nothing can reconstruct. Zero AWS cost: a Scan
  consumes capacity that is already provisioned.
- **Free protections for the data**, all in Terraform and all free:
  `deletion_protection_enabled` on every table, `prevent_destroy` on the three
  irreplaceable ones, and the API role stripped of `DeleteItem` on `Tenants`
  and `Agents` — the request path can delete a whitelist entry and nothing else.
- **`.gitattributes`** — forces LF on `*.sh`. With `core.autocrlf=true` on a
  Windows checkout, every shell script in the repository was checked out CRLF
  and failed under WSL with `set: - : invalid option`. That included
  `scripts/run_tests.sh`, the one command the README tells a newcomer to run.
- **`config/nginx/` rewritten** as a real customer example for the agent's
  enforcement model, with `aiops-agent-geo.conf` as a seed so nginx can start
  before the agent has ever enforced anything. Four tests assert the example
  and the adapter cannot drift apart.

### Changed

- **`ADR-004`** records the first real exception to ADR-002's "0đ, absolutely,
  forever": the deployment package is 62MB zipped, Lambda's direct-upload
  ceiling is 50MB, so the artifact ships through S3 at roughly one US cent a
  month. Recorded rather than buried in a Terraform comment.
- `.env.example` rewritten. It had described the superseded architecture —
  Redis hosts, a Grafana password, a Kubernetes master IP — so anyone filling
  it in was configuring a system that had already been deleted. The backend
  reads exactly three settings.
- README, architecture, runbook, onboarding and `terraform/README.md` had all
  frozen at "Phase 7 has not started" while Phase 7 was writing the
  infrastructure. Corrected; each now separates *written and validated* from
  *run against real AWS*.

### Not done, and it is the point

`docs/deployment.md` is a deployment **design**. The Phase 7 gate has two
failing rows — the post-deploy smoke test and a tested backup restore — and
neither can close without an AWS account. They are recorded as failures rather
than waved through.

## [0.1.0] — 2026-08-24 — hybrid model

Merged to `main` with `--no-ff` and tagged `v0.1.0`. Nothing in this release
has run on real AWS.

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
