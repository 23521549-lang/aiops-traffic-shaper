# Retrospective — from a Kubernetes cluster to a deployed serverless product

Written 2026-09-21, after the Phase 7 gate passed 8/8 against a live
deployment. Sources are `.sdlc/project-state.json`, the seven handoff manifests,
the gate evidence files and the ADRs. Every claim below points at one of them.

This is not a summary of what was built — the manifests already say that.

## 1. What the project carried, and for how long

| Issue | First raised | Closed | Phases carried |
|---|---|---|---|
| Cost threshold for "≈0đ" unquantified | phase-1 | phase-2 (ADR-002: 25 RCU/WCU, 1M req/month) | 1 |
| Retrain writes straight to production, no validation gate | phase-3 | phase-6 (validator ported, 7 tests) | 3 |
| Local venv does not match `requirements.txt` | phase-4 (marked BLOCKER) | phase-5 (clean rebuild; found missing `httpx2`) | 1 |
| US-4 AC3 — nothing throttles at the free-tier ceiling | phase-5 (Must-story, NOT MET) | phase-6 (429 + `Retry-After`) | 1 |
| Branch unmerged to `main` | phase-3 | phase-6 (`--no-ff`, tag `v0.1.0`) | 3 |
| **Nothing has run on real AWS** | phase-2 | **phase-7** | **5** |
| **No edge rate limiting on the public endpoint** | phase-2 (ADR-002 hardening) | **phase-7, partially** — Shield Standard arrived with CloudFront; per-IP application limiting still absent | **5, still open** |
| **PII / compliance for third-party end-user IPs** | phase-1 | **never** | **6, still open** |
| No Cognito Hosted UI; both surfaces paste a token | phase-3 | never | 4, still open |
| Agent fail-open vs fail-closed never confirmed with the developer | phase-1 | never explicitly | 6, still open |
| No supported local application run | phase-6 | never | 1, still open |
| Un-suspend endpoint | phase-6 | never | 1, still open |
| Throttling is global, not per tenant | phase-6 | never | 1, still open |
| E2E mocks `nginx -s reload`; no real nginx driven | phase-5 | never | 2, still open |

Two things stand out.

**The handoff mechanism worked for engineering debt and failed for decisions.**
Every item that was work — the validation gate, throttling, the venv, the
merge — got closed within one to three phases. Every item that was a *decision
somebody had to make* — PII, fail-open semantics, Hosted UI — is still open
after four to six phases. They did not lose to other work; they were never
scheduled, because a manifest can carry a question indefinitely without anyone
noticing it is not moving. The PII question has now been carried through the
entire life of the project and is recorded, again, in the Phase 7 manifest.

**"Nothing has run on real AWS" was carried for five phases and was the root
of almost everything found in Phase 7.** Three defects surfaced in the first
hour of deploying that no amount of local testing could have found (§3). The
cost of carrying it was not the deployment work; it was that five phases of
green tests described a system nobody had ever seen answer a request.

## 2. Gate rows that did not pass cleanly

**Substitutions (4):**

- Phase 4 — `gitleaks` unavailable; the secret scan ran `detect-secrets` plus
  `git log -p`. Recorded as a substitute, not claimed as a gitleaks result.
- Phase 7 row 1, every run — `docker build .` does not apply to a project that
  ships a zip through Mangum; the package build stands in for it.
- Phase 7 row 2 — an **added** row (IaC validity), marked as added, taken from
  the phase reference's built-in IaC review rather than the baseline table.
- Phase 7 row 3 — the CI row cites the last GitHub run *and* re-runs `ci.yml`'s
  checks locally, because the cited run always predates the working tree.

**Phases re-run from scratch (2):** Phases 4 and 5 were completed for the old
single-tenant model on 2026-08-21, then marked `superseded` and redone entirely
on 2026-08-24 for the hybrid model. Both old evidence files are kept.

**Gates that failed before passing (1, three times):** Phase 7 failed 2/8 on
2026-08-24 and again 2/8 on 2026-09-20 — both times on rows 4 and 6, both times
because nothing was deployed. It passed 8/8 on 2026-09-21. **This is the
process working.** Row 4's evidence in run 1 says it plainly: *"a production
Definition of Done that never touched production is not done."* Waving those
rows through as "not applicable" was available twice and taken neither time,
and that refusal is what eventually forced a real deployment.

A fourth run was made the same day for a reason worth recording: run 3 passed
8/8, but its header still read *"Scope note: everything short of an apply. No
AWS account was used"* — a hardcoded `echo` from when that was true — while row
4 was smoke-testing production three lines below. Correct result, self-
contradicting document. Re-run rather than shipped.

**Routing:** phases 1 and 2 ran on `fallback` role, phase 5 ran `degraded`
(`webapp-testing` absent; E2E went through the project's own pytest +
ASGITransport harness), phase 7 ran `fallback` as *primary* by the reference's
own design. Phase 4 was the first phase where the specialist skill (`vibesec`)
was actually invoked — after three earlier runs where it sat installed and
unused, which is what made routing a file on disk instead of a rule in prose.

## 3. Decisions that were reversed

| Decision | Reversed by | Cost |
|---|---|---|
| ADR-001: self-managed Kubernetes on EC2 | ADR-002 (hybrid serverless) | The largest reversal in the project: 119 files deleted, phases 2–5 redone from scratch. Right call — EC2 bills hourly from day one against a "0đ forever" constraint |
| python-jose for JWT | ADR-003 (PyJWT) | Small. jose pinned `pyasn1<0.5.0` and dragged in `ecdsa`, whose advisory has no fix |
| ADR-002's implicit "the zip uploads directly" | ADR-004 (S3) | ~1 US cent/month, and the first admitted exception to a constraint stated as absolute |
| ADR-002's public Function URL with `NONE` auth | ADR-005 (CloudFront + OAC, `AWS_IAM`) | Body hashing in three client-side places and a login form rewritten from a plain HTML post to `fetch` |
| DynamoDB state-lock table | S3 native lock files (`use_lockfile`) | Negative cost: removed a PAY_PER_REQUEST table, the one billing mode ADR-002 singles out as having no free allowance |
| Creating the GitHub OIDC provider | Referencing it | Found at apply time: it is an account-level singleton already owned by another project. Importing it would have started a fight between two Terraform states that neither apply wins |

**One reversal was of a diagnosis, not a decision, and it is the most
instructive.** Phase 7 concluded from five reproducible measurements that the
AWS account blocked anonymous invocation of Lambda function URLs. The evidence
was real and the inference was wrong. It survived because it explained every
observation *and correctly predicted the next failure* — CloudFront with OAC,
built on that theory, also returned 403, which looked like confirmation. The
actual cause was that AWS's documentation lists **two** `add-permission` calls
for a Lambda function URL origin and every Terraform example in circulation
shows one. Granting only `InvokeFunctionUrl` produces a 403 indistinguishable
from an account-level block.

The theory cost about an hour. What ended it was reading the vendor's
documentation instead of reasoning further from symptoms. Recorded in full in
ADR-005 rather than quietly corrected, because a project that keeps its wrong
turns is the only kind that can learn from them.

## 4. What the estimates missed

**Phase 6 was documentation and became a cleanup.** Opening the docs exposed
that the pivot had never been swept up: the only CI workflow deployed the
*superseded* architecture and triggered on push to `main`, so merging the
rebuild would have fired it. `terraform/` and `k8s/` still provisioned a
billable EC2 cluster the README told newcomers to apply. 119 files were deleted
in a phase whose goal was "write the handover package".

**Phase 7 was deployment and became an architecture change.** The plan was
apply, smoke test, done. It produced ADR-005 (a new edge architecture), a
remote-state bootstrap that the deploy pipeline could not have worked without,
three defect fixes, and two portability fixes — every shell script in the
repository was checked out CRLF and unusable under WSL, including the one the
README tells a newcomer to run first.

The pattern in both: **a phase that first *looks* at something long-untouched
inherits everything that was deferred into it.** Phase 6 was the first phase to
read the docs end to end. Phase 7 was the first to run anything outside a test
harness. Neither overran because its own work was underestimated.

## 5. Skill-level observations

Observed in this run only.

1. **"A gate reads, it never repairs" is what produced the deployment.** Rows 4
   and 6 could have been dispositioned "not applicable — no AWS account"
   twice. Holding them at FAIL across two runs is the single mechanism that
   converted a written deployment into a real one.
2. **Gate headers are narration and drifted like narration.** The verdict line
   is computed from a failure counter and stayed correct; the scope note was a
   hardcoded `echo` and went stale, ending up contradicting a row in its own
   file. Anything asserted in an evidence file should be derived, not echoed.
3. **Re-running a phase's gate needs the same superseding discipline as
   re-running a phase.** Four Phase 7 runs exist; the rule as written covers
   phases, and the runs were preserved by judgement rather than by the rule.
4. **The manifest's *Open issues carried forward* has no field for "who
   decides".** Engineering debt closed reliably; questions for the developer
   did not, and the section gives both the same shape. Phase 7's manifest added
   the owner by hand.

## Still open, and who decides

**The publisher (developer) decides:**
- PII / compliance for end-user IPs from third-party tenants flowing through a
  shared backend. Carried since Phase 1.
- Agent fail-open vs fail-closed when the backend is unreachable. The
  implementation fails *open* — existing blocks stand and expire locally — but
  this was never confirmed as the intent.
- Whether per-IP edge rate limiting is worth leaving Always-Free for.

**Engineering decides:**
- No real traffic has been served: one demo tenant, three agents registered,
  and `TelemetryEvents` empty. No telemetry has been scored in production and
  no nightly retrain has run on live data. **The largest remaining gap, and
  the only one on this list that engineering cannot close by itself.**
- **The alert email is unconfirmed, so six armed alarms reach nobody.** AWS
  deletes a pending SNS subscription after 72 hours; the one created on
  2026-09-26 was never confirmed and had been silently removed by 2026-10-01.
  Recreated on 2026-10-01 and pending again. Until somebody clicks the link in
  the mail to the address in `terraform.tfvars`, every alarm below fires into
  an empty topic. This is the same "instrumentation, not observability" shape
  that `alarms.tf` was written to fix, one layer further out, and the comment
  on `aws_sns_topic_subscription.email` had warned about it in advance.
- The deploy workflow has never run. `AWS_DEPLOY_ROLE_ARN` and `AWS_REGION`
  were set as repo variables on 2026-10-01; the `production` environment still
  has **no required reviewers**, so the approval gate the workflow's header
  describes does not exist yet. Deploys have been run from a developer machine
  with Terraform instead.
- No real nginx or iptables has ever been driven by the agent's enforcers.

### Closed since this list was written, and left here because the list was wrong

Seven items above were carried as open long after the work that closed them.
They are recorded rather than deleted, because the failure worth remembering
is not any one of them, it is that a document nobody re-read kept asserting
them while the code disagreed.

| Was listed as open | What is actually true |
|---|---|
| Rollback is a re-apply from a tag, no Lambda alias | Alias `live` exists and tracks a published version (19 at the time of writing), so rollback is a pointer flip. Still untested. |
| Throttling is global, not per tenant | `enforce_tenant_quota` is a dependency on the telemetry route and refuses with 429 at 25% of the daily ceiling, per tenant. Six tests in `test_tenant_quota.py`. |
| No un-suspend endpoint | `POST /admin/v1/tenants/{id}/reactivate`, plus the console equivalent. |
| Nothing polls `/health` or `/ready` | The probe Lambda runs every five minutes on an EventBridge rule and checks `/ready` **through CloudFront**, which is the path a real agent takes. |
| No alarm on the free-tier ceiling | `aiops-traffic-shaper-free-tier-80pct` fires on the probe's `DailyUsageRatio` at 0.8, the same threshold the console banner uses. Six alarms exist in total. |
| The retrain loop walks tenants serially | `dispatch_all` fans out one asynchronous invocation per tenant (`InvocationType="Event"`), so no tenant shares another's 15-minute ceiling. |
| Terraform and `create_all_tables()` both create the tables | Terraform owns the eight tables in production. `create_all_tables()` is now reached only by the test suite and by `scripts/run_local.py`, which is also the supported local run that this list said did not exist. |
| A stale `aiops-traffic-shaper-prod-terraform-lock` table | Deleted 2026-10-01. It held one legacy `-md5` digest row and nothing referenced it: the backend moved to S3-native locking (`use_lockfile = true`), and `PAY_PER_REQUEST` is the one billing mode ADR-002 names as having no Always-Free allowance. |
