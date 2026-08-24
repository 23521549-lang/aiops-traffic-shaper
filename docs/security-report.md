# Security Report — hybrid multi-tenant model

- **Date:** 2026-08-24 · **Phase:** 4 (Security & Review), strict mode
- **Scope:** branch `feature/hybrid-backend` — `services/backend/` and
  `services/agent/`, i.e. every line written in the Phase 3 redo
- **Replaces** the 2026-08-21 report entirely. That one audited the old
  single-tenant K8s model; its assumptions do not carry over. It remains in
  git history (commit `ad73703`).
- **Tooling:** `vibesec` skill (routed via `.sdlc/skills-resolved.json`),
  manual review of the full diff, `pip-audit`, `detect-secrets`,
  `pip-licenses`, `ruff`.

## Findings

| # | Severity | Finding | Location | Status |
|---|---|---|---|---|
| H1 | High | `admin_auth` accepted a Cognito **access token** and tokens minted for any app client in the pool: `token_use` was never checked, and `verify_aud` was switched off whenever `cognito_app_client_id` was unset — which is its default | `api/cognito_auth.py` | **Fixed** |
| H2 | High | Verification algorithm was read from the JWKS entry rather than pinned, on a library with known algorithm-confusion CVEs | `api/cognito_auth.py` | **Fixed** |
| H3 | High | Suspending a tenant did nothing. `agent_auth` checked only the *agent's* status; nothing read the *tenant's*. A suspended tenant kept ingesting telemetry, kept receiving mitigation decisions, and its dashboard JWT stayed valid | `api/dependencies.py`, `api/routes/admin.py` | **Fixed** |
| H4 | High | The agent trusted the backend blindly: `MitigationState.ip` was an unvalidated `str`, and `NginxAdapter` wrote it into `/etc/nginx/conf.d/` then reloaded nginx — a newline injects arbitrary nginx directives on the customer's machine. `IptablesAdapter` had validated from the start; nginx had not | `schemas/mitigation.py`, `agent/enforcer/nginx_adapter.py` | **Fixed** |
| M5 | Medium | 18 known CVEs across 6 declared dependencies, incl. `python-multipart` (DoS, reachable pre-auth at `/ui/login`) and the JWT library itself | `*/requirements.txt` | **Fixed** |
| M6 | Medium | No security headers at all — no CSP, X-Frame-Options, nosniff, HSTS or Referrer-Policy | `main.py` | **Fixed** |
| M7 | Medium | Cookie-authenticated state-changing UI endpoints had no CSRF defence beyond `SameSite=Lax` — no token, no Origin check | `ui/csrf.py` (new), `ui/*.py` | **Fixed** |
| M8 | Medium | `track_usage` wrote to DynamoDB on **every** request including rejected ones, letting anonymous traffic burn the Always-Free quota the product depends on | `main.py` | **Mitigated** |
| L9 | Low | `detail=f"Invalid token: {e}"` returned raw library exception text to the caller | `api/cognito_auth.py` | **Fixed** |

### How each fix was verified
Every finding got a failing test **first**, run against the unpatched code, and
the test names state the vulnerability rather than the mechanism. Auth tests
sign real RS256 tokens with a real locally generated keypair and verify through
the same `jwt.decode()` call production uses — a stubbed verifier would have
hidden H1 entirely. Suite: **142 passed**, `ruff` clean.

## Checks that passed with no finding

- **Tenant isolation (JSON API).** Structurally sound: every `/dashboard/v1/*`
  route derives `tenant_id` from the JWT claim and passes it as the DynamoDB
  partition key. No route on the tenant surface accepts a tenant identifier
  from a path, query or body — there is no IDOR surface to probe. The one
  cross-tenant surface (`/admin/v1/*`) is behind `admin_auth`.
- **Secrets.** `.env` has never been committed (`git log --all -- .env` is
  empty) and is in `.gitignore`. History scan over all commits found only the
  placeholder `GRAFANA_ADMIN_PASSWORD=change-me-grafana-password` in
  `.env.example`. Agent API keys are `secrets.token_urlsafe(32)` (256-bit),
  stored only as SHA-256, and returned exactly once at registration.
- **Licences.** All dependencies MIT / BSD / Apache-2.0. The project is
  open-source (`closed_source: false`), so no copyleft conflict arises.
- **XSS.** Jinja2 autoescaping is on for all `.html` templates; the only
  `innerHTML` write in `interactions.js` consumes server-rendered, escaped
  markup.

## Accepted / deferred risks

| Risk | Why it stands | Owner decision |
|---|---|---|
| No AWS-native rate limiting on the public Lambda Function URL | API Gateway was dropped for cost (ADR-002). M8 removes the *unauthenticated* amplification, but a caller with valid credentials can still spend quota | Carried forward from ADR-002 |
| No Cognito Hosted-UI OAuth flow — CLI and web UI both paste an ID token by hand | Known v1 gap from Stage 8/9, unchanged here | Deferred to a later round |
| Retrain writes straight to `production` with no staging/validation gate | Port of `ai_engine/ml/validator.py` is scope beyond Stage 7 | Backlog, recorded in Stage 7 |

## Open issue that must close before Phase 5

**The local venv does not match `requirements.txt`.** It runs
`fastapi 0.111 / starlette 0.37` while the declared set pins
`fastapi==0.134.0` — the Phase 3 dependency remediation updated the file but
never reinstalled the environment. So the 142 green tests ran against a stack
that is **not** the stack that will deploy. `pip-audit -r` on the declared set
is clean, but that is a static check. Phase 5 must rebuild the venv from
`requirements.txt` and re-run the full suite before its own results mean
anything.

## Tooling note (honest)

`gitleaks` — the tool this project's own gate text prefers — was not installed
and installing an external binary was out of scope for this pass. The secret
scan used `detect-secrets` (pip) plus a `git log -p --all` pattern sweep
instead. The result is credible for this repo's size but is not a gitleaks run,
and should not be recorded as one.
