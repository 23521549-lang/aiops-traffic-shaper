# ADR-001: Tech stack (retro-documented, brownfield confirmation)

- **Date:** 2026-08-20
- **Status:** superseded by ADR-002 (2026-08-21 — project pivoted from
  "self-managed K8s cluster protecting one org's own AWS bill" to a free
  hybrid product; this stack/infra is kept as reference for reusable ML
  logic only, not as the current target architecture)

## Context

This is a brownfield project entering the SDLC process at Phase 2. The stack
was already chosen and implemented before this process started; there is no
greenfield decision to make. This ADR exists to put the *existing* choice on
record (so later phases and future sessions don't re-litigate it) and to note
where the stack diverges from what a fresh `web-saas` preset project would
default to.

Confirmed by reading the actual code (not just the docs) during Phase 2
intake, and by running the full test suite (`scripts/run_tests.sh`, 77/77
passing after the config fix below) against exactly these versions.

## Options considered

Not applicable — this ADR documents an existing decision, not a new one.

## Decision

| Layer | Choice | Notes |
|---|---|---|
| API framework | FastAPI 0.111.0 + Pydantic v2 (2.7.1) | Both services (`ai_engine`, `worker_orchestrator`) |
| ASGI server | Uvicorn 0.29.0 | `--workers 2` in Docker CMD |
| ML | scikit-learn 1.4.2 (IsolationForest) + pandas 2.2.2 + numpy 1.26.4 | ai_engine only |
| State store | Redis 7 (redis-py 5.0.4, async client) | Sole source of runtime state; StatefulSet in k8s |
| Scheduler | APScheduler 3.10.4 (AsyncIOScheduler) | Shadow-mode/retrain jobs, mitigation TTL cleanup |
| K8s client | kubernetes 29.0.0 | worker_orchestrator only, patches `nginx-blocklist` ConfigMap |
| Proxy | Nginx 1.25 | Rate limiting (Layer 1), reload via inotify sidecar, no Docker socket |
| Log shipping | FluentBit 3.0 | Sidecar in the nginx-proxy pod (not a DaemonSet — see architecture.md) |
| Orchestration | Kubernetes 1.29 via kubeadm | Self-managed on EC2, not EKS |
| IaC | Terraform, AWS provider 5.100.0 | VPC/EC2/ECR/S3-backend/security-group/github-oidc modules |
| CI/CD | GitHub Actions + OIDC (no static AWS keys) | `.github/workflows/deploy.yml`: test → build-and-push → deploy |
| Observability | Prometheus + Grafana | 7 alert rules per `docs/mlops-design.md` |
| Test framework | pytest + pytest-asyncio + anyio/trio | 77 tests total (49 ai-engine, 28 worker-orchestrator) |

Runtime target: **Python 3.12** (Dockerfiles use `python:3.12-slim`; CI pins
`python-version: "3.12"`). Note for local dev: Python 3.13 does not have
prebuilt wheels for the pinned numpy/scikit-learn versions and will fail to
install from source — use 3.12 (or WSL, where 3.12 was already available on
this machine) for local work.

## Consequences

- The stack is appropriate for the strict/web-saas quality bar this project
  was assigned (production-facing, internet-exposed, open source): typed
  request/response models via Pydantic, async I/O throughout, structured
  observability already wired in.
- No traditional relational database — Redis is both cache and system of
  record (sliding windows, whitelist, mitigation state, shadow training
  stream). This is a deliberate simplicity trade-off documented in
  `docs/architecture.md` ("Why Redis Sorted Sets") — accepted as-is, not
  revisited here.
- Self-managed Kubernetes via kubeadm (not EKS) trades managed-control-plane
  convenience for lower cost and full control — consistent with the FinOps
  framing of the project itself. Accepted as-is.
- Confirmed gap found during this Phase 2 pass: `services/ai_engine/core/config.py`
  and `services/worker_orchestrator/core/config.py` loaded the repo's shared,
  multi-purpose `.env` (Terraform + CI + app variables combined, per
  `.env.example`'s own "5 NHOM BIEN" documentation) directly as each
  service's Pydantic Settings source, with the default `extra="forbid"`.
  This broke every local test run and app boot the moment a real `.env`
  existed, while never surfacing in CI (checkout has no `.env`) or in the
  Docker image (`.dockerignore` excludes `.env`). Fixed by adding
  `extra="ignore"` to both `Settings.model_config` — verified with a full
  green test run afterward. Tracked as the first item in `docs/PLAN.md`.
