"""Phase 5 performance — measured against the ONE number that matters here.

docs/PRD.md defers the concrete threshold to Phase 2, and ADR-002 sets it: the
whole product lives inside DynamoDB's Always-Free 25 WCU / 25 RCU envelope. So
the performance question for this system is not "how many ms" — it is "how many
DynamoDB operations does a request cost, and does that number grow with attack
volume?" Stage 2's entire aggregation design exists to make the answer "no".

These assertions are the regression guard on that design: if someone later
"simplifies" record_batch back to one write per log line, the cost model breaks
long before latency does, and this fails.

Latency is reported too, since Lambda bills GB-seconds, but it is informational
— there is no user-facing latency SLA in the PRD.
"""
import time

import pytest

from services.backend.api.dependencies import hash_api_key
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, TenantsTable, create_all_tables
from services.backend.main import app
from services.backend.ml.model import ModelManager

from fastapi.testclient import TestClient


class _OpCounter:
    """Counts real DynamoDB API calls via botocore's event system — not a
    guess from reading the code, and not moto-specific. Registered on the
    exact client the app will use: attaching to a fresh boto3.Session counts
    nothing, and every assertion built on it passes vacuously (which is how
    the first version of this file "passed")."""

    def __init__(self, resource):
        self.counts = {}
        resource.meta.client.meta.events.register(
            "provide-client-params.dynamodb.*", self._record)

    def _record(self, params, model, **kwargs):
        self.counts[model.name] = self.counts.get(model.name, 0) + 1

    @property
    def writes(self):
        return sum(v for k, v in self.counts.items()
                   if k in ("PutItem", "UpdateItem", "DeleteItem", "BatchWriteItem"))

    @property
    def reads(self):
        return sum(v for k, v in self.counts.items()
                   if k in ("GetItem", "Query", "Scan", "BatchGetItem"))

    def reset(self):
        self.counts = {}


def _logs(n_lines: int, n_ips: int) -> list[dict]:
    return [{
        "time_iso8601": "2026-08-21T00:00:00Z",
        "remote_addr": f"10.0.0.{i % n_ips}",
        "request_method": "GET", "request_uri": "/a", "status": "200",
        "body_bytes_sent": "512", "request_time": "0.05", "http_user_agent": "ua-1",
    } for i in range(n_lines)]


@pytest.fixture
def metered(dynamo_resource):
    """The app plus a counter on the same boto3 session moto is serving."""
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at="2026-08-21T00:00:00Z")
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", registered_at="2026-08-21T00:00:00Z",
        last_seen_at="2026-08-21T00:00:00Z", agent_version="0.1.0",
        api_key_hash=hash_api_key("rawkey"), status="active",
    )
    ModelManager._cache.pop("t-1", None)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    counter = _OpCounter(dynamo_resource)
    counter.reset()
    return TestClient(app), counter


def _post(client, logs):
    return client.post("/agent/v1/telemetry", json={"logs": logs},
                       headers={"X-Agent-Key": "t-1.rawkey"})


def test_write_cost_does_not_scale_with_attack_volume(metered, capsys):
    """The core cost property. 10 IPs sending 20 lines each must cost the same
    writes as 10 IPs sending 2 lines each — otherwise a real attack, which is
    exactly when line count explodes, is also exactly when the bill does."""
    client, counter = metered

    counter.reset()
    assert _post(client, _logs(20, 10)).status_code == 200
    light = counter.writes

    counter.reset()
    assert _post(client, _logs(200, 10)).status_code == 200
    heavy = counter.writes

    with capsys.disabled():
        print(f"\n  writes: 20 lines/10 IPs -> {light} | 200 lines/10 IPs -> {heavy}")

    assert light > 0, "the op counter recorded nothing - it is not attached to the client under test"
    assert heavy == light, (
        f"write cost scaled with log volume ({light} -> {heavy}) - Stage 2's "
        "aggregation design has regressed and the free-tier budget with it"
    )


def test_write_cost_scales_with_distinct_ips_only(metered, capsys):
    """The design's stated shape: one aggregate write per (tenant, ip, bucket).
    Cost should track distinct IPs, and stay far inside 25 WCU per second."""
    client, counter = metered
    measured = {}
    for n_ips in (1, 5, 10):
        counter.reset()
        _post(client, _logs(100, n_ips))
        measured[n_ips] = counter.writes

    with capsys.disabled():
        print(f"  writes by distinct IPs: {measured}")

    assert measured[1] < measured[5] < measured[10]
    # 25 WCU is the whole account's budget; one batch must not eat it
    assert measured[10] < 25, f"a single batch consumed {measured[10]} writes of a 25 WCU budget"


def test_telemetry_latency_is_recorded(metered, capsys):
    """Informational: no latency SLA exists in the PRD, but Lambda bills
    GB-seconds, so the number belongs in the test report."""
    client, counter = metered
    _post(client, _logs(50, 5))  # warm the model cache and tables

    samples = []
    for _ in range(10):
        start = time.perf_counter()
        _post(client, _logs(50, 5))
        samples.append((time.perf_counter() - start) * 1000)

    samples.sort()
    with capsys.disabled():
        print(f"  telemetry latency (50 lines/5 IPs, n=10): "
              f"median {samples[5]:.1f} ms | max {samples[-1]:.1f} ms")

    assert samples[5] < 3000, "median telemetry latency above a 3s sanity ceiling"
