"""
Local end-to-end test of the real detection -> mitigation pipeline.

Exercises the actual integration between both services — real Redis
sliding window, real feature engineering, a real (freshly trained)
IsolationForest, a real HTTP call from AI Engine to Worker Orchestrator
(routed in-process via httpx.ASGITransport instead of a real socket) — with
only the Kubernetes ConfigMap boundary mocked, since no live cluster is
available in this environment. Substitutes for PLAN.md Stage 6 (deferred,
needs real AWS/kubectl access) as far as this environment allows.

Both apps' lifespans and every HTTP/Redis call in this test run on ONE
asyncio event loop (this test function's own) — NOT via starlette's
TestClient, which spawns its own background thread + event loop per
instance. Nesting two TestClients caused "Future attached to a different
loop" errors, since each app's real Redis connection gets bound to
whichever loop was running when its lifespan started. Driving both apps'
lifespans directly and calling through httpx.ASGITransport keeps everything
on one loop, which is also what makes it possible for AI Engine's real
outbound HTTP call to actually reach Worker Orchestrator's real app code.

Requires a real local Redis (same as scripts/run_tests.sh).
"""
from contextlib import AsyncExitStack
from unittest.mock import MagicMock, patch

import httpx
import numpy as np
import pytest
import redis.asyncio as aioredis
from sklearn.ensemble import IsolationForest

from prometheus_client import REGISTRY

from services.ai_engine.main import app as ai_app, lifespan as ai_lifespan
from services.ai_engine.core.config import settings as ai_settings
from services.ai_engine.ml import monitoring as ai_monitoring
from services.ai_engine.ml import registry as ai_registry

# NOTE: ai_engine and worker_orchestrator each define their own
# nginx_blocked_ips_total/nginx_rate_limited_ips_total/
# estimated_cloud_cost_saved_usd Prometheus Gauges/Counters with identical
# names. In production they run in separate processes, so this never
# collides. Importing both apps in this one test process would raise
# "Duplicated timeseries in CollectorRegistry" — unregister ai_engine's
# copies first, since this test only asserts on Worker Orchestrator's
# metrics/state. Worth flagging as a real (if low-impact) naming overlap
# for a future cleanup — not fixed here, out of this test's scope.
for _metric_name in (
    "nginx_blocked_ips_total",
    "nginx_rate_limited_ips_total",
    "estimated_cloud_cost_saved_usd",
):
    _collector = getattr(ai_monitoring, _metric_name)
    REGISTRY.unregister(_collector)

from services.worker_orchestrator.main import (  # noqa: E402
    app as worker_app,
    lifespan as worker_lifespan,
)

ATTACKER_IP = "203.0.113.66"  # TEST-NET-3, RFC 5737 — safe non-routable test IP

# Captured before any patching — services.ai_engine.core.http_client patches
# the same shared `httpx` module's AsyncClient attribute, so code below must
# use this original reference instead of calling httpx.AsyncClient directly
# (which would recurse into the patch).
_RealAsyncClient = httpx.AsyncClient


@pytest.fixture
def anyio_backend():
    # redis.asyncio (used by both real services here) only supports the
    # asyncio event loop, not trio — restrict this module to asyncio only.
    return "asyncio"


def _train_and_promote_baseline_model(tmp_path, monkeypatch):
    """Train a real IsolationForest on 'normal' traffic and promote it to
    production, using tmp_path-isolated registry paths (same technique as
    tests/ai-engine/test_registry.py)."""
    base = tmp_path / "models"
    monkeypatch.setattr(ai_registry, "MODEL_BASE_PATH", base)
    monkeypatch.setattr(ai_registry, "PRODUCTION_PATH", base / "production")
    monkeypatch.setattr(ai_registry, "STAGING_PATH", base / "staging")
    monkeypatch.setattr(ai_registry, "ARCHIVE_PATH", base / "archive")

    rng = np.random.default_rng(42)
    # 7 features: request_rate, error_ratio, avg_bytes_sent, avg_request_time,
    # unique_uri_ratio, user_agent_entropy, post_ratio — "normal" browsing.
    normal = rng.normal(
        loc=[2.0, 0.02, 4000.0, 0.08, 0.6, 0.5, 0.05],
        scale=[0.5, 0.01, 500.0, 0.02, 0.1, 0.1, 0.02],
        size=(200, 7),
    )
    model = IsolationForest(n_estimators=100, contamination=0.01, random_state=42)
    model.fit(normal)

    meta = ai_registry.ModelMetadata(
        version="v_e2e_baseline",
        trained_at="2026-01-01T00:00:00+00:00",
        training_samples=200,
        contamination=0.01,
        score_mean=float(np.mean(model.decision_function(normal))),
        score_std=float(np.std(model.decision_function(normal))),
        features=[
            "request_rate", "error_ratio", "avg_bytes_sent", "avg_request_time",
            "unique_uri_ratio", "user_agent_entropy", "post_ratio",
        ],
        stage="production",
    )
    ai_registry.save_model(model, meta, stage="production")


def _ddos_batch(ip: str, n_requests: int = 50) -> dict:
    """A burst of near-identical, high-rate, single-URI requests from one
    IP — the volumetric-DDoS pattern request_rate/unique_uri_ratio are
    designed to detect (see docs/mlops-design.md Feature Registry).

    Each log line's timestamp differs by a microsecond fraction so the
    JSON-serialized record is unique — Redis Sorted Set members must be
    unique, and 50 byte-identical members would collapse into one entry.
    """
    logs = []
    for i in range(n_requests):
        logs.append({
            "time_iso8601": f"2026-01-01T00:00:00.{i:06d}+07:00",
            "remote_addr": ip,
            "request_method": "GET",
            "request_uri": "/",
            "status": "200",
            "body_bytes_sent": "50",
            "request_time": "0.001",
            "http_user_agent": "attack-bot/1.0",
        })
    return {"logs": logs}


@pytest.fixture
async def clean_redis():
    """Real local Redis — clear any leftover state for both test IPs before
    and after each test so runs don't interfere with each other."""
    r = aioredis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
    ips = (ATTACKER_IP, "203.0.113.67")
    keys = [f"{prefix}:{ip}" for ip in ips for prefix in ("window", "mitigation")]
    await r.delete(*keys)
    yield r
    await r.delete(*keys)
    await r.aclose()


async def _post_telemetry(payload: dict) -> httpx.Response:
    """POST to the real AI Engine app in-process, on the current event loop."""
    transport = httpx.ASGITransport(app=ai_app)
    async with _RealAsyncClient(
        transport=transport, base_url="http://ai-engine.testserver"
    ) as client:
        return await client.post(
            "/api/v1/telemetry",
            json=payload,
            headers={"X-Internal-Token": ai_settings.internal_secret},
        )


@pytest.mark.anyio
async def test_ddos_burst_is_detected_and_mitigated_end_to_end(
    tmp_path, monkeypatch, clean_redis
):
    _train_and_promote_baseline_model(tmp_path, monkeypatch)

    # Route AI Engine's HTTP call to Worker Orchestrator's real ASGI app
    # in-process instead of a real socket (no "worker-orchestrator" DNS
    # entry exists in this environment).
    worker_transport = httpx.ASGITransport(app=worker_app)

    def make_client(timeout: float = 5.0) -> httpx.AsyncClient:
        return _RealAsyncClient(
            transport=worker_transport,
            base_url=ai_settings.worker_orchestrator_url,
            timeout=timeout,
        )

    # Only the Kubernetes boundary is mocked — no live cluster available.
    # Both tiers' patchers are mocked since the exact IsolationForest score
    # (and therefore which tier fires) depends on the trained baseline and
    # isn't worth pinning down exactly here — unit tests already cover
    # tier-threshold classification precisely (test_model.py). This test's
    # job is proving the real cross-service pipeline reacts at all.
    mock_patcher = MagicMock()
    mock_patcher.add_rule.return_value = True
    mock_ratelimit_patcher = MagicMock()
    mock_ratelimit_patcher.add_rule.return_value = True

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(worker_lifespan(worker_app))
        await stack.enter_async_context(ai_lifespan(ai_app))
        stack.enter_context(patch(
            "services.ai_engine.core.http_client.httpx.AsyncClient",
            side_effect=make_client,
        ))
        stack.enter_context(patch(
            "services.ai_engine.api.routes.telemetry.settings.shadow_mode", False
        ))
        stack.enter_context(patch(
            "services.worker_orchestrator.api.routes.mitigate._patcher", mock_patcher
        ))
        stack.enter_context(patch(
            "services.worker_orchestrator.api.routes.mitigate._ratelimit_patcher",
            mock_ratelimit_patcher,
        ))

        response = await _post_telemetry(_ddos_batch(ATTACKER_IP))

    assert response.status_code == 200
    body = response.json()
    assert "Mitigated: 1" in body["message"], body["message"]

    # Real Redis now reflects the real Worker Orchestrator's decision —
    # whichever tier the real model's score actually triggered.
    mitigation_value = await clean_redis.get(f"mitigation:{ATTACKER_IP}")
    assert mitigation_value in ("blocked", "rate_limited"), mitigation_value

    # The (mocked) Kubernetes boundary was reached with the attacker IP, on
    # the patcher matching whichever tier actually fired.
    if mitigation_value == "blocked":
        mock_patcher.add_rule.assert_called_once_with(ATTACKER_IP)
        mock_ratelimit_patcher.add_rule.assert_not_called()
    else:
        mock_ratelimit_patcher.add_rule.assert_called_once_with(ATTACKER_IP)
        mock_patcher.add_rule.assert_not_called()


@pytest.mark.anyio
async def test_normal_traffic_is_not_mitigated_end_to_end(
    tmp_path, monkeypatch, clean_redis
):
    _train_and_promote_baseline_model(tmp_path, monkeypatch)
    normal_ip = "203.0.113.67"

    worker_transport = httpx.ASGITransport(app=worker_app)

    def make_client(timeout: float = 5.0) -> httpx.AsyncClient:
        return _RealAsyncClient(
            transport=worker_transport,
            base_url=ai_settings.worker_orchestrator_url,
            timeout=timeout,
        )

    mock_patcher = MagicMock()
    mock_ratelimit_patcher = MagicMock()

    # A handful of slow, varied requests — looks like a real browsing
    # session, not an attack.
    logs = [
        {
            "time_iso8601": f"2026-01-01T00:00:00.{i:06d}+07:00",
            "remote_addr": normal_ip,
            "request_method": "GET",
            "request_uri": f"/page-{i}",
            "status": "200",
            "body_bytes_sent": str(4000 + i * 50),
            "request_time": "0.08",
            "http_user_agent": "Mozilla/5.0 (real browser)",
        }
        for i in range(5)
    ]

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(worker_lifespan(worker_app))
        await stack.enter_async_context(ai_lifespan(ai_app))
        stack.enter_context(patch(
            "services.ai_engine.core.http_client.httpx.AsyncClient",
            side_effect=make_client,
        ))
        stack.enter_context(patch(
            "services.ai_engine.api.routes.telemetry.settings.shadow_mode", False
        ))
        stack.enter_context(patch(
            "services.worker_orchestrator.api.routes.mitigate._patcher", mock_patcher
        ))
        stack.enter_context(patch(
            "services.worker_orchestrator.api.routes.mitigate._ratelimit_patcher",
            mock_ratelimit_patcher,
        ))

        response = await _post_telemetry({"logs": logs})

    assert response.status_code == 200
    assert "Mitigated: 0" in response.json()["message"]

    mitigation_value = await clean_redis.get(f"mitigation:{normal_ip}")
    assert mitigation_value is None
    mock_patcher.add_rule.assert_not_called()
    mock_ratelimit_patcher.add_rule.assert_not_called()
