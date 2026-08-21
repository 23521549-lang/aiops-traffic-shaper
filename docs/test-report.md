# Test Report

Coverage snapshot from Phase 3 / PLAN.md Stage 5, plus the Phase 5 (Testing
& QA) E2E and performance pass. PLAN.md Stage 6 (E2E against a *live AWS
cluster*) remains deferred — no AWS/kubectl access in this environment —
so Phase 5's E2E work here is a **local substitute**: real Redis, real
trained ML model, real HTTP between both real service apps, only the
Kubernetes ConfigMap boundary mocked. See "End-to-end tests" below.

## How to run

```bash
bash scripts/run_tests.sh
```

Runs both suites against the pinned dependency versions (Python 3.12,
matching the Docker images and CI) with a real local Redis. On Windows,
run via WSL — Python 3.13 (native Windows here) has no prebuilt wheels for
the pinned numpy/scikit-learn versions.

For a coverage report, add `pytest-cov` (not in `requirements.txt`, only
needed for local coverage runs) and run per-service:

```bash
pip install pytest-cov
pytest tests/ai-engine/ --cov=services.ai_engine --cov-report=term-missing
pytest tests/worker-orchestrator/ --cov=services.worker_orchestrator --cov-report=term-missing
```

(Run each service's directory separately — `tests/ai-engine/test_api.py`
and `tests/worker-orchestrator/test_api.py` share a basename and collide if
collected together in one invocation without `__init__.py` files.)

## Results (2026-08-21)

| Suite | Tests | Status |
|---|---|---|
| AI Engine | 96 | all passing |
| Worker Orchestrator | 41 | all passing |
| Integration (local E2E) | 2 | all passing |
| **Total** | **139** | |

(Up from 77 at the Phase 2 handoff — Stage 1 added 2, Stage 2 added 16 across
`test_mitigation.py`/`test_api.py`/new `test_cleanup.py`, Stage 5 added 42
across 5 new unit test files, Phase 5 added 2 local E2E tests.)

## End-to-end tests (Phase 5)

`tests/integration/test_detection_to_mitigation_e2e.py` — the real
detection→mitigation pipeline, run locally as a substitute for PLAN.md
Stage 6 (deferred, needs live AWS/kubectl access this environment doesn't
have). What's genuinely real in this test, not mocked:

- A real local Redis (sliding window, mitigation state).
- A real `IsolationForest`, freshly trained on synthetic "normal" traffic
  and promoted to production via the real `registry` module.
- A real HTTP call from AI Engine to Worker Orchestrator — routed via
  `httpx.ASGITransport` instead of a real socket (no `worker-orchestrator`
  DNS entry exists here), but the actual ASGI request/response cycle,
  routing, and business logic on both sides is real.
- Both apps' real FastAPI lifespans (startup/shutdown), run on one asyncio
  event loop so their Redis connections don't cross loop boundaries.

Only the Kubernetes ConfigMap boundary is mocked (`_patcher`/
`_ratelimit_patcher`) — no live cluster available.

Two scenarios, matching the two Must-behaviors this system exists for:
1. **DDoS burst is detected and mitigated** — 50 rapid identical requests
   from one IP trigger a real anomaly score, a real HTTP mitigate call, and
   land as `blocked`/`rate_limited` in real Redis (whichever tier the
   score actually crosses — unit tests in `test_model.py` already pin down
   exact tier thresholds, this test just proves the pipeline reacts).
2. **Normal traffic is not mitigated** — 5 slow, varied requests from a
   different IP produce a normal score; no mitigation call is made, Redis
   state stays clean.

A real, if minor, cross-service issue surfaced while building this test:
AI Engine and Worker Orchestrator each define Prometheus metrics with
identical names (`nginx_blocked_ips_total`, `nginx_rate_limited_ips_total`,
`estimated_cloud_cost_saved_usd`) — harmless when they run in separate
processes (production), but importing both apps into one Python process
raises "Duplicated timeseries in CollectorRegistry". Worked around in the
test file (unregisters AI Engine's copies before importing Worker's app);
not fixed in the services themselves, since it's not a bug in production
topology — just a naming overlap worth a namespace prefix (e.g.
`ai_engine_*` / `worker_*`) if this ever becomes a real headache.

## Performance (Phase 5, 2026-08-21)

`docs/architecture.md`'s "under 3s end-to-end, 5s budget" refers to the
*whole pipeline* including FluentBit's ~1s batching interval and Kubernetes
ConfigMap propagation (~1-2s, inotify + nginx reload) — neither measurable
without a live cluster (Stage 6). What **is** measurable here is whether
the application logic itself is anywhere near that budget. Measured with
real code (WSL, Python 3.12):

| Operation | Latency | Budget (from architecture.md) |
|---|---|---|
| `_compute_features` (30-record window) | 0.018 ms | ~10 ms |
| `IsolationForest.decision_function` (batch of 100) | 1.86 ms | ~20 ms |
| `IsolationForest.decision_function` (1 vector) | 1.06 ms | ~20 ms |
| Full `/telemetry` round trip (real Redis + real ML + real HTTP call to Worker Orchestrator + Worker's own Redis write) | 7.54 ms | — |

The application layer uses roughly **0.25% of the 3-second budget** — the
real bottleneck, if the 3s target is ever missed in production, will be
FluentBit's batching interval or ConfigMap/Nginx reload propagation, not
this code. Worth re-measuring on the live cluster during Stage 6 to
confirm the infra-layer numbers match the architecture doc's estimates.

## Leak check (Phase 5)

Reviewed all new test files and the captured log output from the E2E runs
above for secrets/PII: only the test placeholder `test-secret-for-ci` /
`test-secret` (matching `.github/workflows/deploy.yml`'s own CI placeholder)
appears, never a real credential. Test IPs use `203.0.113.0/24`
(RFC 5737 TEST-NET-3, reserved for documentation — never a real address).
No PII anywhere in fixtures or logs.

## Coverage by service

| Service | Coverage | Target |
|---|---|---|
| AI Engine | 82% | 80% — met |
| Worker Orchestrator | 94% | 80% — met |

### AI Engine — remaining gap: `ml/training.py` (32%, 64/94 statements uncovered)

Everything else is at or above 81%. `training.py` orchestrates
`run_baseline_training`/`run_daily_retrain`/`start_training_scheduler` — it
touches real `IsolationForest.fit()`, the APScheduler job registration, and
the registry/validator modules together. It's the least-covered module
precisely because it's the most expensive to test in isolation (needs a
real Redis Stream read, a real model fit, and registry/validator
coordination). Overall AI Engine coverage already clears the 80% target
without it, so this was left as a follow-up rather than done here — a
reasonable next task would be integration-style tests that seed a fake
Redis Stream and assert on the promotion decision, rather than trying to
unit-test each internal step.

### Worker Orchestrator — no significant gaps

`blocklist.py` (88%) and `configmap_patcher.py` (90%) are the only files
below 95%, both just missing a couple of error-handling branches
(`ApiException` paths) that aren't worth contriving synthetic k8s API
failures for at this stage.

## Notable finding from writing these tests

`tests/ai-engine/test_api.py`'s original `patch("...get_redis", ...)`
pattern does not actually override the FastAPI dependency — `Depends(get_redis)`
binds the real function object at import time, so patching the module
attribute afterward never reaches it (confirmed by making a test hang on a
real network call to a stale kubeconfig cluster IP during Stage 2 — see
`docs/PLAN.md` Stage 2). All new tests in this stage instead use
`app.dependency_overrides[get_redis] = lambda: mock_redis`, the pattern
`tests/worker-orchestrator/test_api.py` already used correctly throughout.
The original AI Engine tests still pass only because they happen to run
against a real, empty local Redis — worth fixing as its own small task if
this repo's test isolation matters going forward (not done here, out of
this stage's scope).
