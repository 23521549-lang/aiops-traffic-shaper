# Onboarding — picking up this codebase

For a developer inheriting the project. Read this, then `README.md`. Half an
hour total, and you will know where everything is and what you must not break.

## Get it running first

```bash
bash scripts/run_tests.sh
```

Expect **172 passed, ~98% coverage**. If that works you have a complete
development environment; nothing else is needed — no AWS account, no Redis, no
cluster. If it does not work, that is a bug in `requirements.txt`, not in your
machine (it happened once already; see the runbook).

Then read [architecture.md](architecture.md). Twenty minutes there saves you a
day of reading source.

## Where the real decisions live

Code tells you *what*; these tell you *why*, and they are the difference
between changing this system and fighting it.

| Where | What it holds |
|---|---|
| `docs/adr/` | Architecture decisions with the options that were rejected and why |
| `.sdlc/manifests/phase-N.md` | What each development phase decided, produced, and left unfinished |
| `.sdlc/gate-evidence/phase-N.txt` | Raw command output proving each phase's quality gate actually ran |
| `docs/security-report.md`, `docs/test-report.md` | Audit and verification results, including what failed |

Files named `*-superseded-*` are history from before the architecture pivot.
They are kept deliberately. Do not update them; they describe the past
correctly.

## Five invariants — break these and something important breaks quietly

**1. Write cost must not scale with log volume.** Telemetry is aggregated into
one item per (tenant, IP, time bucket) with atomic `ADD`. If you ever find
yourself writing per log line, the free-tier budget — the constraint the whole
architecture exists to satisfy — is gone. `test_perf_budget.py` fails first.

**2. `tenant_id` comes from the verified token, never from the request.** Every
tenant-facing query uses it as the DynamoDB partition key. The moment a route
accepts a tenant identifier from a path, query or body, multi-tenant isolation
is over. There is currently no such route; keep it that way.

**3. The agent does not trust the backend.** Anything bound for an nginx config
is validated as an IP address on the agent side too, not only server-side. The
agent runs with elevated privileges on someone else's machine.

**4. The model cache is deliberate.** `ModelManager` caches per tenant across
warm Lambda invocations. It looks like a premature optimisation; it is the
difference between one DynamoDB read per container and one per request. Do not
"simplify" it away — the comment there says so for this reason.

**5. Evidence, not narration.** A test suite that was green an hour ago is not
evidence that it is green now. This is a project habit, and it is why the bugs
in `CHANGELOG.md` under *Fixed* were found at all.

## How work is organised

The project runs a 7-phase lifecycle (`/sdlc`) with a quality gate between
phases; `.sdlc/project-state.json` records where it is. You do not need the
tooling to contribute — but it explains the file layout, and CI enforces the
same checks the gates do:

| CI step | Gate it mirrors |
|---|---|
| `ruff check` | Phase 3 — lint clean |
| `pytest --cov-fail-under=80` | Phase 3 suite + Phase 5 coverage |
| `pip-audit` | Phase 4 — dependency audit clean |

Deployment is a second workflow (`deploy.yml`) and it fires on nothing: a
human dispatches it and a human approves the GitHub `production` environment.
Do not restore an automatic trigger — the previous workflow of that name would
have deployed the superseded architecture the moment the rebuild merged.

So a red CI and a failed gate mean the same thing. The dependency audit blocks
on purpose: a newly disclosed upstream CVE will fail an unrelated PR. Relaxing
that needs a recorded decision, not a quiet edit.

Tests are written **before** the fix, and they are named after the defect
rather than the mechanism — `test_admin_auth_rejects_cognito_access_token`,
not `test_auth_2`. Skim `test_cognito_auth.py` for the house style.

## What is unfinished

Ordered by how much it will surprise you:

1. **Nothing has ever run on real AWS.** Every test uses `moto` and locally
   signed JWTs. The infrastructure is now fully written — `terraform/`
   describes all 7 tables, both Lambdas, Cognito, the schedule and the alarms,
   and `terraform validate` passes — but no `terraform apply` has happened.
   Two things therefore remain unproven no matter how green the suite is: the
   post-deploy smoke test and a backup restore. They are the two open rows on
   the Phase 7 gate, and neither can close without an AWS account.
2. **No supported way to run the application locally.** The app needs real
   DynamoDB and Cognito; the temporary mock harness was removed. Restoring one
   is a good first task and would pay for itself immediately.
3. **No Cognito Hosted UI.** Both the CLI and the web login take a pasted ID
   token.
4. **Throttling is global, not per tenant.** It protects the bill, not
   fairness: one noisy tenant can pause ingest for everyone.
5. **No un-suspend endpoint.** Suspension is one-way today.
6. **Two owners for table creation.** Terraform describes the 7 tables and
   `create_all_tables()` creates them from application code. Whichever runs
   first wins. Nobody has decided which should own it, and only a real
   deployment will show which actually runs first.
7. **PII is an open question.** The backend stores end-user IPs and the agent
   logs them. `docs/PRD.md` raises it under Constraints and leaves it open; it
   is a compliance decision, not a technical one.

## Good first tasks

- Restore a supported local run (item 2 above) — self-contained, and everyone
  after you benefits.
- Add an un-suspend endpoint with tests; the suspend path shows the shape.
- Make throttling per tenant rather than global.

Each is small, has a clear finish line, and touches enough of the system to
teach you the layout.
