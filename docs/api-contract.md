# API Contract

Two internal services, not public-facing (both sit behind the Nginx proxy /
K8s NetworkPolicy — see `docs/architecture.md` "Security Model"). Verified
against actual route/schema code, not just design docs.

## Overview (both services)
- Base URL: `/api/v1` (plus unauthenticated `/health` and `/metrics` at root)
- Auth: header `X-Internal-Token: <INTERNAL_SECRET>`, validated by
  `core/security.py::verify_internal_token` via a FastAPI `Security`
  dependency (`InternalAuth`). Missing or wrong token → `401`.
- Format: JSON, UTF-8
- Error envelope: FastAPI default — `{"detail": "<message>"}` on
  `HTTPException` (404) and Pydantic's standard `{"detail": [...]}` array on
  `422` validation errors. No custom error envelope exists in code — do not
  assume the `{"error": {...}}` shape without confirming it's still wanted.

---

## AI Engine (`services/ai_engine`)

| Method | Path | Description | Auth | Request body | Response 200 | Errors |
|---|---|---|---|---|---|---|
| GET | /health | Liveness probe | no | — | `{"status": "healthy"}` | — |
| GET | /metrics | Prometheus exposition | no | — | text/plain metrics | — |
| POST | /api/v1/telemetry | Ingest a batch of Nginx access-log records; runs reputation check → feature extraction → ML scoring → mitigation trigger | yes | `TelemetryBatch` | `TelemetryResponse` | 401, 422 |
| POST | /api/v1/whitelist | Add IP to whitelist + training exclusion | yes | `WhitelistRequest` | `{"message": str}` | 401, 422 (invalid IP) |
| DELETE | /api/v1/whitelist/{ip} | Remove IP from whitelist | yes | — | `{"message": str}` | 401, 404 |
| GET | /api/v1/whitelist | List whitelisted + training-excluded IPs | yes | — | `{"whitelisted_ips": str[], "training_excluded": str[]}` | 401 |
| DELETE | /api/v1/whitelist/{ip}/training-exclusion | Re-include an IP in future training (does not un-whitelist it) | yes | — | `{"message": str}` | 401, 404 |
| GET | /api/v1/model/status | Current model metadata + shadow-mode flag | yes | — | see schema below | 401 |
| POST | /api/v1/model/retrain | Fire-and-forget manual retrain (`asyncio.create_task`, response returns immediately) | yes | — | `{"message": str}` | 401 |
| POST | /api/v1/model/promote | Promote staging model to production if one exists | yes | — | `{"message": str}` | 401 |

### Schemas

```yaml
LogRecord:                    # one raw Nginx access-log entry
  time_iso8601: string
  remote_addr: string
  request_method: string
  request_uri: string
  status: string               # coerced from int if needed
  body_bytes_sent: string       # coerced from int if needed
  request_time: string          # coerced from float if needed
  http_user_agent: string

TelemetryBatch:
  logs: LogRecord[]

TelemetryResponse:
  received: int                # logs received in this batch
  processed_ips: int            # ML-processed + reputation-blocked count
  message: string                # human-readable summary incl. circuit breaker state

WhitelistRequest:
  ip: string                    # validated via ipaddress.ip_address, 422 on invalid
  reason: string = ""

ModelStatus:                   # GET /model/status response, untyped dict in code
  model_ready: bool
  shadow_mode: bool
  version: string | null
  trained_at: string | null      # ISO 8601
  training_samples: int | null
  contamination: float | null
  score_mean: float | null
  score_std: float | null
```

---

## Worker Orchestrator (`services/worker_orchestrator`)

| Method | Path | Description | Auth | Request body | Response 200 | Errors |
|---|---|---|---|---|---|---|
| GET | /health | Liveness probe | no | — | `{"status": "healthy"}` | — |
| GET | /metrics | Prometheus exposition | no | — | text/plain metrics | — |
| POST | /api/v1/mitigate | Apply Tier 1 (rate-limit key) or Tier 2 (ConfigMap deny rule + Nginx reload) mitigation; no-ops if IP is whitelisted | yes | `MitigationRequest` | `MitigationResponse` | 401, 422 |
| GET | /api/v1/blocklist | List active mitigations (scans `mitigation:*`) | yes | — | `BlocklistEntry[]` | 401 |
| DELETE | /api/v1/blocklist/{ip} | Manually remove a mitigation (deletes key + removes ConfigMap deny rule + reloads Nginx) | yes | — | `{"message": str}` | 401, 404 |
| POST | /api/v1/whitelist | Add IP to whitelist (this service's own copy of the endpoint — same Redis key as AI Engine's) | yes | `WhitelistRequest` | `{"message": str}` | 401, 422 |
| DELETE | /api/v1/whitelist/{ip} | Remove IP from whitelist | yes | — | `{"message": str}` | 401, 404 |
| GET | /api/v1/whitelist | List whitelisted IPs | yes | — | `{"whitelisted_ips": str[]}` | 401 |

### Schemas

```yaml
MitigationTier:                # IntEnum
  RATE_LIMIT: 1
  HARD_BLOCK: 2

MitigationRequest:
  target_ip: string             # validated via ipaddress.ip_address, 422 on invalid
  reason: string
  tier: MitigationTier = 1       # RATE_LIMIT default

MitigationResponse:
  target_ip: string
  tier: int
  action: string                 # "skipped" | "rate_limited" | "blocked"
  ttl_seconds: int
  whitelisted: bool = false
  message: string

BlocklistEntry:
  ip: string
  tier: int                      # 1 or 2, derived from Redis value
  ttl_remaining: int             # seconds, from Redis TTL
```

Note: this service duplicates the AI Engine's `/whitelist` endpoints against
the same `whitelist:ips` Redis key, but without the
`training:excluded_ips` side effect (that concept only exists in AI Engine).
Calling `POST /whitelist` on the Worker Orchestrator will NOT exclude the IP
from ML training — only the AI Engine's `/whitelist` endpoint does that. Two
separate entry points writing the same key with different side effects is a
latent footgun; flagged for `docs/PLAN.md`.

## Conventions
- No pagination on any list endpoint (`/whitelist`, `/blocklist`) — both
  return unbounded lists today. Fine at current scale (Redis Set/SCAN), would
  need addressing if reputation/blocklist counts grow large.
- No API versioning beyond the static `/v1` prefix — no version negotiation.
- No idempotency keys — not needed given the mutation shapes (Set add/remove,
  key set-with-TTL are naturally idempotent).
