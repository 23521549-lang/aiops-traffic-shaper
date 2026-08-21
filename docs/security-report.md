# Security Report

## Code review of Phase 3 changes (Phase 4, 2026-08-21)

Reviewed the full diff introduced in Phase 3 (whitelist-suppression check,
generalized+debounced `ConfigMapPatcher`, Tier 1 dynamic rate-limit wiring,
`pydantic-settings` config fix) twice independently — once directly, once
via a separate subagent with no access to the first pass's reasoning — for
injection, authN/authZ bypass, secrets exposure, and RBAC over-permissioning.

**Result: no findings at ≥7/10 confidence.** Specifically traced:
- Every IP value that reaches `ConfigMapPatcher.add_rule()` (which builds
  the Nginx rule line via `rule_template.format(ip=ip)`) originates from
  `MitigationRequest.target_ip`, validated by `ipaddress.ip_address()` in
  `services/worker_orchestrator/schemas/mitigation.py` before it can reach
  the handler body — no template/injection path found.
- `remove_rule()` (used by the unblock/cleanup paths) never formats the
  rule template at all, only does a dict-key lookup — no injection surface
  there either, including via the pre-existing unvalidated `ip: str` path
  param on `DELETE /blocklist/{ip}` (unchanged by this diff).
- `k8s/worker-orchestrator/rbac.yaml`'s `resourceNames: ["nginx-blocklist",
  "nginx-ratelimit"]` scoping was already present before this diff (not
  broadened) — the new ConfigMap the patcher targets was already
  anticipated in RBAC.
- The whitelist-suppression check reads the same `whitelist:ips` Redis key
  already gated by `X-Internal-Token` for writes — no new way to reach it.
- The `extra="ignore"` pydantic-settings fix only affects unknown env vars;
  `internal_secret` has no default, so a missing/misnamed value still
  fails fast rather than silently falling back to something insecure.

Excluded from scope per standard review criteria: the debounce window's
theoretical add/remove race (availability edge case, no attacker-granted
access, and overlaps with rate-limiting/DOS exclusions), and the
`$binary_remote_addr`-vs-`X-Forwarded-For` caveat already documented in
`docs/architecture.md` (DOS/rate-limiting-adjacent, out of scope for this
pass).

## Dependency + secret scan (Phase 3 / PLAN.md Stage 4 prep)

Scope: dependency vulnerability scan + secret history check, done ahead of
this formal review.

## Dependency vulnerability scan

Run via `pip-audit` (WSL, Python 3.12, against the exact pinned
`requirements.txt` for each service) on 2026-08-21.

### services/ai_engine/requirements.txt

| Package | Installed | Advisory | Fixed in |
|---|---|---|---|
| scikit-learn | 1.4.2 | PYSEC-2024-110 | 1.5.0 |
| starlette (transitive, via fastapi==0.111.0) | 0.37.2 | PYSEC-2026-161 | 1.0.1 |
| starlette | 0.37.2 | PYSEC-2026-248 | 1.3.0 |
| starlette | 0.37.2 | PYSEC-2026-249 | 1.3.1 |
| starlette | 0.37.2 | PYSEC-2026-1943 | 0.40.0 |
| starlette | 0.37.2 | PYSEC-2026-1941 | 0.47.2 |
| starlette | 0.37.2 | PYSEC-2026-2281 | 1.1.0 |
| starlette | 0.37.2 | PYSEC-2026-2280 | 1.1.0 |

### services/worker_orchestrator/requirements.txt

Same starlette advisories as above (transitive via `fastapi==0.111.0`,
which every service pins). No `kubernetes`/`redis`/`apscheduler`/
`prometheus-client` findings.

### Remediation status: APPLIED 2026-08-21

- **scikit-learn 1.4.2 → 1.5.0** in `services/ai_engine/requirements.txt`
  (the minimum version that fixes PYSEC-2024-110, not the latest available
  — chosen to minimize behavioral-drift risk from the bump).
- **fastapi 0.111.0 → 0.134.0** in both services' `requirements.txt`, which
  resolves `starlette` 0.37.2 → 1.6.0 — comfortably above every "fixed in"
  threshold from the advisories above. Checked with a smaller bump first
  (fastapi 0.115–0.120 range only reaches starlette 0.48.0, still short of
  the 1.0.1/1.1.0/1.3.x fixes) before settling on 0.134.0 as a middle
  ground between "smallest possible diff" and "latest available"
  (0.141.1 at time of writing).
- **pydantic / pydantic-settings left unpinned-change** (2.7.1 / 2.2.1) —
  confirmed compatible with fastapi 0.134.0, no forced bump needed.

**Verification:** fresh WSL venv per service with the new pins,
`pip-audit` re-run against the actual `requirements.txt` files — **no
known vulnerabilities found** for either service. Full test suite
(`bash scripts/run_tests.sh`) re-run afterward: 96 (ai-engine) + 41
(worker-orchestrator) = 137 passed, no regressions from the bump.

## Secret history check (broad pattern scan, Phase 4)

`git log -p --all` scanned for `(api_key|secret|password|token)\s*[:=]\s*<value>`
patterns across every commit, not just the 3 known filenames below. Single
match: `token: fake-token` in the CI workflow's dummy kubeconfig
(`.github/workflows/deploy.yml`) — a deliberate placeholder for tests, not
a real credential. No other matches.

## Secret history check (specific filenames)

```
git log --all --full-history -- .env        → no results (never committed)
git log --all --full-history -- "*.pem"      → no results (never committed)
git log --all --full-history -- terraform.tfvars → no results (never committed)
```

Confirmed clean: `.env`, private key files, and `terraform.tfvars` have
never entered git history. `.gitignore` correctly excludes all three
patterns, and the real local copies of these files were verified present
on disk (used during earlier live-cluster work per Phase 2 findings) but
outside version control the entire time.

## Dependency license audit (Phase 4, 2026-08-21)

Ran `pip-licenses` against every installed dependency (direct + transitive)
across both services' virtualenvs. **No GPL/AGPL/LGPL (copyleft) licenses
found** — everything is MIT, BSD (2/3-clause), Apache-2.0, MPL-2.0, ISC,
PSF-2.0, or Unlicense. This means whichever license the project eventually
picks (LICENSE decision still deferred, see below) will not conflict with
any dependency's terms — no copyleft obligations to reconcile.

## LICENSE

Deferred per user decision 2026-08-20 (see `docs/PLAN.md` Stage 4) —
explicitly non-blocking for this report or Stage 5. Dependency license
audit above confirms this choice is unconstrained by dependency licensing.
