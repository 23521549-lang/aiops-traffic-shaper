# Implementation Plan

Brownfield project. Phase 2 intake found the codebase substantively complete
and well-tested once one local-only config bug was fixed — this plan is the
remaining work to take it from "code exists and passes tests" to
"verified running in production, OSS-ready, at the project's declared
`web-saas` / strict quality bar" (see `.sdlc/project-state.json`).

Each stage lists a goal, file/module scope, and a checkpoint command that
proves the stage is done. Stages are ordered by dependency.

## Stage 0 — Unblock local dev and verify the existing test suite [DONE]

**Goal:** confirm the codebase actually runs, not just that it reads well.

**Scope:** `services/ai_engine/core/config.py`,
`services/worker_orchestrator/core/config.py`.

**What happened:** both services' `Settings` loaded the repo's shared,
multi-purpose `.env` (Terraform + CI + app vars combined) with
pydantic-settings' default `extra="forbid"`, so `Settings()` raised a
`ValidationError` on import the moment a real `.env` existed — breaking
every local test run and app boot. Fixed by adding `extra="ignore"` to
`model_config` on both. See `docs/adr/001-tech-stack.md` for the full
root-cause writeup.

**Checkpoint (met 2026-08-20):** `bash scripts/run_tests.sh` exits 0 —
`49 passed` (ai-engine) + `28 passed` (worker-orchestrator), run against the
pinned dependency versions, Python 3.12, and a real Redis instance (not
mocked infra, matching what CI already does).

## Stage 1 — Suppress mitigation calls for whitelisted IPs (hybrid fix) [DONE]

**Goal:** close gap (a) from `docs/schema.md` without losing shadow-stream
training data. Decision made 2026-08-20 (user-directed, hybrid approach —
see `docs/schema.md` "Gaps" for the full rationale).

**Scope:** `services/ai_engine/api/routes/telemetry.py`,
`services/ai_engine/ml/monitoring.py` (new counter).

**Design:**
- Keep feature computation + ML scoring running for *every* IP, including
  whitelisted ones — the shadow stream / drift signal must not be lost.
- Add the whitelist check only at the mitigation-trigger boundary: once an
  IP scores anomalous in `telemetry.py`, `SISMEMBER whitelist:ips` before
  calling `_trigger_mitigation`. Whitelisted → skip the HTTP call to the
  Worker Orchestrator entirely. This check only runs on already-anomalous
  IPs (a small subset of traffic), so no caching layer is needed.
- Add a new Prometheus counter `ai_whitelist_suppressed_total`, incremented
  every time this skip fires — gives operators a signal for tuning
  thresholds ("the model wants to block this whitelisted IP N times/hour").

**Tests to add** (`tests/ai-engine/test_api.py`):
- `test_whitelisted_ip_not_mitigated` — anomalous + whitelisted IP →
  `_trigger_mitigation`/circuit breaker never called.
- `test_whitelisted_ip_still_scored` — anomalous + whitelisted IP → feature
  vector still computed and still written to `training:shadow_data`.

**Checkpoint:** both new tests pass; `bash scripts/run_tests.sh` still
exits 0 with the two new tests included.

## Stage 2 — Tier 1 dynamic per-IP rate limiting (real enforcement) [DONE]

**Goal:** close gap (b) from `docs/schema.md`. Decision made 2026-08-20:
this is not intentional bookkeeping — Tier 1 was meant to trigger a real,
dynamic, per-IP stricter rate limit and that wiring was never built. Build
it, symmetric with how Tier 2 already patches the blocklist ConfigMap.

**Scope:** `k8s/nginx/configmap-nginx.yaml`, new `k8s/nginx/configmap-ratelimit.yaml`
(or equivalent), `services/worker_orchestrator/orchestrator/configmap_patcher.py`,
`services/worker_orchestrator/api/routes/mitigate.py`, the TTL cleanup job
(`orchestrator/cleanup.py`), `docs/architecture.md`.

**Design (as implemented — two deviations from the original spec, both
forced by how the existing `ConfigMapPatcher` actually stores rules, caught
during implementation):**
1. **Nginx config** (`config/nginx/nginx.conf`, mirrored in
   `k8s/nginx/configmap-nginx.yaml`):
   ```nginx
   geo $strict_ip {
       default 0;
       include /etc/nginx/ratelimit/*;
   }
   map $strict_ip $strict_key {
       1 $binary_remote_addr;
       0 "";
   }
   limit_req_zone $strict_key zone=ml_tier1:10m rate=${NGINX_STRICT_RATE_LIMIT_RPS}r/s;
   ```
   Two deviations from the original spec:
   - **Glob include (`/etc/nginx/ratelimit/*`), not a single named file.**
     `ConfigMapPatcher` stores one ConfigMap *key* per IP (`data[ip_key] =
     rule`), which becomes one *file* per IP when mounted — exactly how
     `nginx-blocklist` → `/etc/nginx/blocklist.d/*` already works. An
     exact-filename include (`strict-ips.conf`) would never match any file
     the patcher actually writes. Fixed to glob, matching the established
     blocklist pattern exactly.
   - **Zone named `ml_tier1`, not `strict`.** `strict_limit` already exists
     (static, path-based limit for `/login`, `/api/auth`, `/admin`) —
     naming the new zone `strict` too would be confusing to read even
     though there's no technical collision. `ml_tier1` makes the ML-vs-path
     distinction explicit. Documented in `docs/architecture.md`.

   In `location /` (`default.conf` / `configmap-nginx.yaml`), alongside the
   existing static limit: `limit_req zone=ml_tier1 burst=${NGINX_STRICT_RATE_LIMIT_BURST} nodelay;`.
   An empty `$strict_key` makes Nginx skip the zone entirely, so `ml_tier1`
   only ever applies to IPs actually present in the ConfigMap — no risk of
   it silently rate-limiting everyone. Note: the sensitive-path location
   redefines its own `limit_req` (just `strict_limit`), so per Nginx's
   directive-inheritance rules `ml_tier1` does not additionally apply
   there — acceptable, since those paths already carry the same 5r/s cap.
2. **New ConfigMap** `nginx-ratelimit` (`k8s/nginx/configmap-ratelimit.yaml`,
   starts as `data: {}` — same as `nginx-blocklist`), mounted at
   `/etc/nginx/ratelimit/`. Reloader sidecar's `inotifywait` extended to
   watch both `/etc/nginx/blocklist.d` and `/etc/nginx/ratelimit`.
3. **Generalized `configmap_patcher.py`** into one class parameterized by
   `rule_template`, instantiated twice: blocklist (`deny {ip};`) and
   ratelimit (`{ip} 1;`). Methods renamed `add_rule`/`remove_rule` (from
   `add_deny_rule`/`remove_deny_rule`) since they're no longer blocklist-
   specific — all call sites and tests updated.
4. **`mitigate.py` Tier 1 branch**: now symmetric with Tier 2 — checks
   Redis for an existing `rate_limited` state before calling the ratelimit
   patcher's `add_rule` + triggering an Nginx reload (avoids redundant
   patches on repeat detections of the same already-limited IP).
5. **`blocklist.py` `unblock_ip`**: now calls `remove_rule` on *both*
   patchers unconditionally (safe no-op if the IP isn't present in one of
   them) — a manual unblock no longer only lifts a Tier 2 hard block while
   leaving a Tier 1 rate limit in place.
6. **TTL cleanup job** (`cleanup.py`): extended to also remove expired
   Tier 1 entries from the ratelimit ConfigMap. Since an *already-expired*
   Redis key can no longer be read for its tier, cleanup tries
   `remove_rule` on both patchers for every expired IP (each is a no-op if
   the IP isn't in that particular ConfigMap) rather than guessing the
   tier.
7. **Debounced flush**: `ConfigMapPatcher.add_rule`/`remove_rule` queue a
   change and schedule `flush()` `debounce_seconds` (default 3s) later via
   `asyncio`, coalescing a burst of per-IP changes into one ConfigMap patch.
   `cleanup.py` calls `flush()` explicitly at the end of each sweep instead
   of waiting on the timer, since it already runs on its own interval.
8. **Doc note** added to `docs/architecture.md`: `$binary_remote_addr` is
   only correct when traffic hits the NodePort directly; switch to a
   validated `X-Forwarded-For` if a load balancer is ever placed in front.

**Tests added** (92 total passing, up from 77 at Phase 2 handoff):
- `tests/worker-orchestrator/test_mitigation.py::TestConfigMapPatcher` —
  rewritten for `add_rule`/`remove_rule` + debounce (`flush()` called
  explicitly in tests to force-apply queued changes), plus new tests for
  custom rule templates and coalescing multiple pending changes into one
  patch call.
- `tests/worker-orchestrator/test_api.py` — `test_tier1_patches_ratelimit_configmap`,
  `test_tier1_skips_ratelimit_patch_when_already_active`,
  `test_unblock_removes_from_both_configmaps`; existing Tier 2 test updated
  for the renamed method.
- `tests/worker-orchestrator/test_cleanup.py` (new file, `cleanup.py` had
  no tests before this stage) — expired-IP removal from both ConfigMaps,
  one flush per sweep, non-expired keys untouched.
- E2E confirmation deferred to Stage 6 (needs a live cluster): a Tier
  1-mitigated IP must actually receive `429`/`503` once it exceeds 5 r/s —
  the nginx directives were reviewed manually for syntax correctness
  (matches Nginx's documented geo+map conditional-rate-limit pattern) but
  never run against a real `nginx -t` in this environment (no nginx binary
  available locally, and installing one via WSL `sudo apt-get` prompted for
  a password non-interactively rather than proceeding — not forced).

**Checkpoint (met 2026-08-21):** all new/updated unit tests pass;
`bash scripts/run_tests.sh` exits 0 with 51 (ai-engine) + 41
(worker-orchestrator) = 92 passed.

## Stage 3 — Verify real AWS/Kubernetes deployment state [DEFERRED]

**Deferred 2026-08-21 (user decision):** no AWS/kubectl access in this
environment. Not blocking the Phase 3→4 gate — tracked here for the user
to run when they have cluster access, per the checkpoint below.

**Goal:** resolve the open question from Phase 2 intake — is the cluster
described in README.md ("Deployed and verified stable on AWS
ap-southeast-1") actually live right now? This could not be determined from
the repo alone (no AWS CLI/credentials in this environment; Terraform state
lives in S3, not locally).

**Scope:** requires the user's AWS access, not code changes.

**Checkpoint:** run `bash scripts/health-check.sh` against the real cluster
(needs a working `kubectl` context) — or, if the cluster was torn down,
`terraform plan` in `terraform/` to see current vs. desired state. Record
the outcome in `docs/runbook.md` and correct README.md's status section to
match reality (dated, not just "stable" in the present tense indefinitely).

## Stage 4 — Security & OSS-readiness gate prep (feeds Phase 4) [DONE]

**Goal:** this project is open source (`closed_source: false`) and
internet-facing — close the gaps that matter before a security review.

**Scope:** repo root, `docs/`.

1. **LICENSE — deferred, non-blocking.** User decision 2026-08-20: pick a
   license later; this item does not block any other stage in this plan or
   the Phase 4 gate itself, it's just tracked here so it isn't forgotten
   before any public release/announcement of the repo.
2. Dependency vulnerability scan: `pip-audit -r services/ai_engine/requirements.txt`
   and same for `worker_orchestrator` — neither has been run per repo
   evidence. Record results in `docs/security-report.md` (created in
   Phase 4).
3. Confirm no secrets ever entered git history (`.env`, `terraform.tfvars`,
   `*.pem` are gitignored and were never tracked per `git status` — worth a
   `git log --all --full-history -- .env` sanity check before going public).

**Checkpoint (met 2026-08-21, remediation applied same day in Phase 4):**
`pip-audit` initially found 11 advisories in ai_engine (scikit-learn 1.4.2,
starlette 0.37.2 transitive), 9 in worker_orchestrator (starlette only).
Fixed by bumping `scikit-learn` to 1.5.0 and `fastapi` to 0.134.0 (→
starlette 1.6.0) in both services — `pip-audit` re-run against the actual
`requirements.txt` files now reports no known vulnerabilities for either
service, full test suite (137 tests) still green. Secret-history check
clean (`.env`, `terraform.tfvars`, `*.pem` never entered git history).
Full detail in `docs/security-report.md`. LICENSE still explicitly
deferred, not required for this checkpoint.

## Stage 5 — Raise test coverage to the project's 80% target [DONE]

**Goal:** `coverage_target` in `.sdlc/project-state.json` is 80%; no
coverage tooling exists yet (`pytest-cov` not in either `requirements.txt`,
no `.coveragerc`/`pyproject.toml` config).

**What was done:** `pytest-cov` used locally (not added to
`requirements.txt` — only needed for coverage runs, not normal test runs).
Baseline was AI Engine 63%, Worker Orchestrator 94% (already well above
target thanks to Stage 1/2 work). Added 5 new AI Engine test files:
- `tests/ai-engine/test_whitelist.py` — the `/whitelist` routes had zero
  tests before this (36 stmts, was 53%, now 100%).
- `tests/ai-engine/test_model_routes.py` — `/model/status|retrain|promote`
  routes, same gap (30 stmts, was 50%, now 100%).
- `tests/ai-engine/test_http_client.py` — full `CircuitBreaker` state
  machine (closed→open→half-open→closed/open), previously only exercised
  incidentally (59 stmts, was 37%, now 97%).
- `tests/ai-engine/test_validator.py` — `validate_model`'s block-rate and
  std-regression rejection paths, previously untested (32 stmts, was 28%,
  now 100%).
- `tests/ai-engine/test_registry.py` — save/load/promote round-trips
  against a real (tmp_path-isolated) filesystem and a real tiny
  `IsolationForest`, monkeypatching `registry.PRODUCTION_PATH` etc. instead
  of mocking joblib (96 stmts, was 36%, now 94%).

**Checkpoint (met 2026-08-21):** AI Engine 82%, Worker Orchestrator 94% —
both ≥80%. Full detail in `docs/test-report.md`. Remaining known gap:
`ml/training.py` (32%) — the retrain orchestration module, expensive to
unit-test in isolation (real Redis Stream + real model fit + registry/
validator coordination); left as a documented follow-up since overall
target is already met without it.

## Stage 6 — End-to-end verification against a live cluster [DEFERRED]

**Deferred 2026-08-21 (user decision), same reason as Stage 3.**

**Goal:** unit tests mock Redis/K8s — they prove the code's logic, not that
the detection→mitigation pipeline works end-to-end. The repo already has
the tools for this (`scripts/load-test.sh`, `scripts/simulate-attack.sh`);
they just haven't been run against a confirmed-live cluster in this session.

**Scope:** requires Stage 3's live cluster.

**Checkpoint:**
- `bash scripts/simulate-attack.sh http://<worker-ip>:30080 all` against
  the real deployment, followed by checking Grafana
  (`ai_anomalies_detected_total`, `nginx_blocked_ips_total`) for the
  expected signal.
- Tier 1 E2E from Stage 2: an IP mitigated at Tier 1 must actually receive
  `429`/`503` once it exceeds `NGINX_STRICT_RATE_LIMIT_RPS` (5 r/s) —
  proves the dynamic ratelimit ConfigMap wiring works end-to-end, not just
  in unit tests.

This is the PRD-equivalent acceptance test for a project that has no formal
PRD (brownfield, retro-documented per `.sdlc/project-state.json`
phase_history).

## Stage 7 — Docs polish for public/OSS handover [DEFERRED]

**Deferred 2026-08-21 (user decision):** depends on Stage 3/6 output
(README status correction needs a confirmed live-or-not answer). Revisit
together with Stage 3/6.

**Goal:** once Stages 3–6 land, close the loop on documentation accuracy.

**Scope:** `README.md`, add `CONTRIBUTING.md` and `SECURITY.md` (standard
for a public OSS repo accepting external issues/PRs).

**Checkpoint:** README's "System Status" section reflects a dated, verified
state (not an undated "stable" claim); `CONTRIBUTING.md` and `SECURITY.md`
exist.
