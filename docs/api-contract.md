# API Contract — hybrid backend (single Lambda, public-facing)

> Supersedes the old two-internal-service contract (`worker_orchestrator` +
> `ai_engine` behind a private K8s NetworkPolicy). The hybrid backend is a
> **single public Lambda** behind a Function URL (see ADR-002), reached
> directly by agents on the open internet plus two authenticated UIs.
> Endpoints are namespaced by audience since there is no network boundary
> to separate them anymore.

## Overview
- Base URL: one Lambda Function URL, e.g. `https://<id>.lambda-url.<region>.on.aws`
- Format: JSON, UTF-8
- Error envelope: FastAPI default — `{"detail": "<message>"}` (404/other
  `HTTPException`), Pydantic's `{"detail": [...]}` array on 422. Kept
  identical to the old services' convention on purpose — no reason to change it.
- Auth: two schemes, see below. No shared internal-network trust boundary
  exists anymore (old `X-Internal-Token` model is gone) — every request must
  authenticate itself.

### Auth schemes
| Scheme | Used by | How |
|---|---|---|
| Agent API key | `/agent/v1/*` | Header `X-Agent-Key: <tenant_id>.<api_key>`; validated against `Agents.api_key_hash` (ADR-002/schema.md) |
| Cognito JWT | `/dashboard/v1/*`, `/admin/v1/*` | Header `Authorization: Bearer <id_token>`; `/admin/v1/*` additionally requires the Cognito `admin` group claim. Only **ID tokens** are accepted — `token_use`, `aud` and `iss` are all verified, and auth fails closed if the pool is unconfigured (Phase 4 / H1). The server-rendered UI additionally accepts the same token from an `id_token` cookie, because plain browser navigation cannot set a header |

---

## Agent-facing (`/agent/v1`) — called by the lightweight agent on the tenant's own system

| Method | Path | Description | Auth | Request body | Response 200 | Errors |
|---|---|---|---|---|---|---|
| POST | /agent/v1/register | One-time registration, issued via the CLI at install time | Cognito JWT (tenant owner, interactive) | `AgentRegisterRequest` | `AgentRegisterResponse` (api_key shown once) | 401, **403** (tenant suspended), 422 |
| POST | /agent/v1/telemetry | Batch-ingest request metadata; runs feature extraction → per-tenant model scoring → mitigation decision | Agent API key | `TelemetryBatch` | `TelemetryResponse` (includes per-IP decisions to enforce locally) | 401, **403** (tenant suspended), **422**, **429** (daily free-tier ceiling reached — carries `Retry-After`) |
| GET | /agent/v1/decisions | Pull current active mitigation state for this tenant (used on agent restart/reconnect, so enforcement survives a crash without waiting for new telemetry). Deliberately NOT throttled: an overload must not silently drop protection already in force | Agent API key | — | `MitigationState[]` | 401, **403** (tenant suspended) |

## Dashboard-facing (`/dashboard/v1`) — end user, tenant-scoped only

| Method | Path | Description | Auth | Request body | Response 200 | Errors |
|---|---|---|---|---|---|---|
| GET | /dashboard/v1/mitigations | Active rate-limit/block decisions for the caller's own tenant | Cognito JWT | — | `MitigationState[]` | 401 |
| GET | /dashboard/v1/whitelist | List whitelisted IPs | Cognito JWT | — | `{"whitelisted_ips": str[]}` | 401 |
| POST | /dashboard/v1/whitelist | Add IP to whitelist + training exclusion | Cognito JWT | `WhitelistRequest` | `{"message": str}` | 401, 422 |
| DELETE | /dashboard/v1/whitelist/{ip} | Remove IP from whitelist | Cognito JWT | — | `{"message": str}` | 401, 404 |
| GET | /dashboard/v1/model/status | This tenant's own model metadata | Cognito JWT | — | `ModelStatus` | 401 |

## Control-Platform-facing (`/admin/v1`) — publisher only, cross-tenant

| Method | Path | Description | Auth | Request body | Response 200 | Errors |
|---|---|---|---|---|---|---|
| GET | /admin/v1/tenants | List all tenants | Cognito JWT (admin group) | — | `Tenant[]` | 401, 403 |
| GET | /admin/v1/agents | List all agents across tenants, via `Agents.LastSeenIndex` GSI (schema.md) | Cognito JWT (admin group) | — | `AgentSummary[]` | 401, 403 |
| GET | /admin/v1/usage | Today's + recent `UsageCounters` vs. Always-Free ceilings (Lambda 1M req / 400,000 GB-s per month, DynamoDB 25 RCU/WCU) | Cognito JWT (admin group) | — | `UsageReport` | 401, 403 |
| POST | /admin/v1/tenants/{tenant_id}/suspend | Suspend a tenant (abuse control — protects the shared 0đ backend from one tenant's runaway traffic). Also **revokes every agent API key** the tenant holds | Cognito JWT (admin group) | — | `{"message": str, "agents_revoked": int}` | 401, 403, 404 |

## Health

| Method | Path | Description | Auth |
|---|---|---|---|
| GET | /health | Liveness | no |

---

## Schemas

```yaml
# --- Agent ---
AgentRegisterRequest:
  agent_label: string           # human-readable, e.g. "prod-web-1"

AgentRegisterResponse:
  tenant_id: string
  agent_id: string
  api_key: string                # shown once, not retrievable again — matches Agents.api_key_hash

LogRecord:                       # carried over from the old TelemetryBatch shape, unchanged
  time_iso8601: string
  remote_addr: string
  request_method: string
  request_uri: string
  status: string
  body_bytes_sent: string
  request_time: string
  http_user_agent: string

TelemetryBatch:
  logs: LogRecord[]

TelemetryResponse:
  received: int
  processed_ips: int
  decisions: MitigationState[]   # NEW vs. old contract — agent enforces these locally, no separate /mitigate call

# --- Mitigation / shared ---
MitigationState:                 # mirrors the DynamoDB MitigationState item, schema.md
  ip: string
  tier: int                      # 0=normal, 1=rate-limit, 2=hard-block
  score: float
  reason: string
  expires_at: int                 # epoch seconds

WhitelistRequest:
  ip: string                      # validated via ipaddress.ip_address, 422 on invalid
  reason: string = ""

ModelStatus:
  model_ready: bool
  shadow_mode: bool
  version: string | null
  trained_at: string | null
  training_samples: int | null
  contamination: float | null
  score_mean: float | null
  score_std: float | null

# --- Control Platform ---
Tenant:
  tenant_id: string
  name: string
  status: string                  # active | suspended
  created_at: string

AgentSummary:
  tenant_id: string
  agent_id: string
  agent_version: string
  status: string                  # active | stale | revoked
  last_seen_at: string

UsageReport:
  date: string
  total_requests: int
  estimated_gb_seconds: float
  dynamodb_consumed_rcu: float
  dynamodb_consumed_wcu: float
  ceiling_warning: bool           # true if any dimension is above an alert threshold (Phase 3 to define, e.g. 80%)
```

## Conventions
- No pagination on `/admin/v1/tenants` or `/admin/v1/agents` yet — carried
  over as a known gap from the old contract; revisit once tenant count is
  large enough for it to matter (same call as before, not re-litigated).
- No API versioning beyond the static `/v1` prefix per audience namespace.
- Idempotency: agent registration is a one-time interactive action (not
  retried automatically); telemetry ingestion is naturally idempotent per
  event (`event_ts` in `TelemetryEvents`, schema.md).

## Open item carried from the old contract

The old contract flagged that `worker_orchestrator` and `ai_engine` both
wrote the same `whitelist:ips` Redis key with different side effects (only
one excluded from training) — a latent footgun. The hybrid contract
collapses both into a single `/dashboard/v1/whitelist` endpoint precisely
to remove this footgun, not carry it forward.

## Browser surfaces (not part of the JSON contract)

Stage 9 added server-rendered HTML pages served by the same Lambda:
`/ui/login`, `/ui/logout`, `/ui/static/interactions.js`, `/dashboard/ui*` and
`/admin/ui*`. They are listed here only so nobody mistakes them for missing
API: they return HTML, authenticate via the `id_token` cookie, and their
state-changing requests additionally require a CSRF double-submit token
(`X-CSRF-Token` echoing the `csrf_token` cookie). The JSON API deliberately has
no CSRF requirement — a cross-site form cannot set an `Authorization` header,
and demanding a token there would break the agent CLI for no security gain.

*Contract verified against the implementation on 2026-08-24 (Phase 6 gate row
2). The four drifts found — telemetry 429/403, register 403, decisions 403, and
suspend's `agents_revoked` — are corrected above.*
