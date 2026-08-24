# Test Report — hybrid multi-tenant model

- **Date:** 2026-08-24 · **Phase:** 5 (Testing & QA), strict mode
- **Scope:** branch `feature/hybrid-backend` — `services/backend/`, `services/agent/`
- **Replaces** the 2026-08-21 report, which tested the old single-tenant K8s
  model (preserved in git history at `aa0391d`).
- **Routing:** phase 5 ran **degraded** — `webapp-testing` (Playwright) is not
  installed, so E2E uses the project's own pytest + real-ASGI harness, the
  pattern Stage 9 already proved here.

## Headline

**151 passed · 98% line coverage · target was 80%**

Run on a **clean venv built from `requirements.txt`**, not the developer's
working environment. That distinction matters more than the numbers.

## The environment finding that came first

Phase 4 handed over a blocker: the local venv did not match `requirements.txt`
(it ran `fastapi 0.111 / starlette 0.37` while the file declared
`fastapi 0.134`). Every green run previously reported, back through Phase 3,
was measured on a stack that would never be deployed.

Rebuilding from the declared file exposed a real defect immediately:

```
RuntimeError: The starlette.testclient module requires the httpx2 package
```

`requirements.txt` was **incomplete** — starlette 1.x needs `httpx2` for its
TestClient and nothing declared it. On a clean machine the suite could not even
be *collected*. Fixed by pinning `httpx2==2.12.0`. The declared set now
installs, runs, and audits clean.

This is the class of problem the Phase 6 gate ("README verified from a clean
clone") exists to catch — found one phase early.

## Coverage

| | |
|---|---|
| Statements | 2477 |
| Missed | 57 |
| **Line coverage** | **98%** |
| Target (project state) | 80% |

38 files sit at 100%. The residue is defensive branches — `except OSError`
paths, the real-AWS-only JWKS fetch, adapter failure logging. This is the first
coverage measurement in the project's history; nothing was tuned to reach the
number, it is a by-product of the TDD discipline held since Stage 1.

## E2E — Must-story acceptance criteria

`services/backend/tests/test_e2e_agent_backend.py` drives the **agent's own
code** (Collector, CLI, DecisionStore, NginxAdapter) against the **real
backend** (real ASGI app, real routes, real auth, real DynamoDB via moto, real
ModelManager). The single mocked boundary is `nginx -s reload`, which needs
root and a live nginx.

| Story | Acceptance criterion | Case | Result |
|---|---|---|---|
| US-3 | agent sends telemetry, receives decisions | full loop: batch → score → decision → `deny <ip>;` on disk → TTL sweep unblocks | pass |
| US-3 | agent works independently when the backend is unreachable | dead backend: existing blocks stand, local expiry still runs, agent does not crash | pass |
| US-4 | tenant telemetry is isolated | two tenants, two agents: each sees only its own decisions on the agent API | pass |
| US-4 | (Phase 4 control) suspension takes effect | agent working a moment ago is refused on its next call, keys revoked | pass |
| US-5 | one command registers an agent | CLI `register` → real backend → issued key authenticates a real telemetry post | pass |
| US-5 | CLI reports failure clearly | bad token → non-zero exit, clear message, no half-written config | pass |

**Honest caveat:** all six passed on their first run. They are
*characterization* tests — they lock in behaviour that was already correct,
unlike the Phase 4 tests which went red first and drove a fix. Worth having as
regression guards, but they discovered nothing.

## Performance — measured against the cost model, not a latency SLA

The PRD sets no latency SLA and defers the concrete threshold to Phase 2;
ADR-002 sets it as the DynamoDB Always-Free envelope, **25 WCU / 25 RCU**. The
performance question for this system is therefore how many DynamoDB operations
a request costs, and whether that grows under attack.

`services/backend/tests/test_perf_budget.py` counts real DynamoDB API calls
through botocore's event system:

| Measurement | Result |
|---|---|
| 20 log lines / 10 IPs | **11 writes** |
| 200 log lines / 10 IPs | **11 writes** |
| Writes by distinct IPs | 1 IP → 2 · 5 IPs → 6 · 10 IPs → 11 |
| Telemetry latency (50 lines / 5 IPs, n=10) | median **32 ms**, max 41 ms |

**Write cost is flat in log volume and scales only with distinct IPs** — Stage
2's entire design goal, now measured rather than asserted. Cost must not spike
during an attack, which is precisely when line count explodes. The assertions
are permanent regression guards: if anyone "simplifies" `record_batch` back to
per-line writes, the cost model breaks here first.

One test in this file initially **passed vacuously** (`0 == 0`) because the op
counter was attached to the wrong boto3 session and counted nothing. Fixed, and
a `light > 0` guard added so it can never pass on an inert counter again.

## Leak check

| Check | Result |
|---|---|
| Secrets in logs | none — no logger call touches `api_key`, token, password or `X-Agent-Key` |
| Real credentials in fixtures | none — no AWS/Stripe/GitHub/PEM-shaped material |
| PII in logs | **present by design, and worth a decision** — see below |

The agent logs end-user IP addresses (`services/agent/enforcer/__init__.py`,
`ip=%s` on apply/unblock). The backend does **not** log IPs. That distinction
matters: the PII stays on the customer's own machine rather than landing in the
publisher's logs. The backend does *store* IPs in DynamoDB, which is inherent
to what the product does.

`docs/PRD.md` raised this exact question under Constraints and left it open —
"cần xác nhận nếu backend xử lý dữ liệu traffic thật của bên thứ ba (có thể
chứa PII, vd IP người dùng cuối của khách hàng)". Phase 5 cannot close it: it
is a compliance decision for the publisher, not a test result. Flagged here so
it does not quietly disappear.

**Tool substitution:** `varlock`, the skill routed for this row, ships a CLI
that is not installed, and installing it was out of scope. The checks above
were run as targeted source scans instead — recorded as a substitution, not
claimed as a varlock run. One varlock recommendation worth adopting later: a
typed `.env.schema` marking each variable sensitive or not. The project has
`.env.example`, which declares names but not sensitivity.

## Open issues carried to Phase 6

- **Still no real AWS.** Everything is moto plus locally signed JWTs. No
  Cognito, no Lambda, no live DynamoDB has ever run. US-9 ("bằng chứng chạy
  thật") remains untouched and is the developer's to close.
- The E2E suite mocks `nginx -s reload`; no test has ever driven a real nginx
  or a real iptables rule.
- `httpx2` was missing from `requirements.txt` until today, so no clean-machine
  install had ever been verified. Phase 6's clean-clone check should expect
  more of this kind.
- ~~Free-tier throttling (US-4 AC3) is a warning only.~~ **Closed in Phase 6**
  (`test_usage_throttle.py`, 6 cases): telemetry ingest is refused with `429`
  + `Retry-After` at 100% of the daily share, while reads keep serving. The
  limiter is global rather than per tenant, which is a known coarseness, not a
  gap in the criterion.
