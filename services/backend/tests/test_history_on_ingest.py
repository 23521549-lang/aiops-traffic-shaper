"""History is written on the ingest path, and must never break it.

Two writes are added to telemetry ingest: one episode row per anomalous IP,
and one rollup row per batch. Both are reporting. Ingest is the product —
it is the path that returns the decisions a customer's nginx enforces.

So the rule this file exists to pin: **a failed history write degrades to a
log line, never to a 500.** DynamoDB throttles rather than bills when a
table exceeds its provisioned capacity, and an attack is exactly when the
episode write is burstiest and exactly when the agent most needs its
decisions back.
"""
import time

import pytest

from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantHistoryTable, TenantsTable, create_all_tables,
)


@pytest.fixture
def wired(dynamo_resource, monkeypatch):
    from services.backend.api.dependencies import hash_api_key

    create_all_tables(dynamo_resource)
    now = "2026-09-23T00:00:00Z"
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at=now)
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01", registered_at=now,
        last_seen_at=now, agent_version="1.0.0",
        api_key_hash=hash_api_key("rawkey"), status="active")
    return dynamo_resource


def _batch(n=3, ip="203.0.113.7"):
    from services.backend.schemas.telemetry import LogRecord, TelemetryBatch
    return TelemetryBatch(logs=[
        LogRecord(remote_addr=ip, time_iso8601="2026-09-23T12:00:00Z",
                  request_method="POST", request_uri=f"/login?{i}", status=401,
                  body_bytes_sent=90, request_time=0.003, http_user_agent="curl")
        for i in range(n)
    ])


def test_a_batch_records_its_traffic(wired):
    from services.backend.api.routes.agent import ingest_telemetry

    ingest_telemetry(_batch(4), tenant_id="t-1", resource=wired)

    now = int(time.time())
    series = TenantHistoryTable(wired).query_series("t-1", now - 3600, now)
    assert len(series) == 1
    assert int(series[0]["requests"]) == 4
    assert int(series[0]["batches"]) == 1


def test_traffic_accumulates_across_batches(wired):
    from services.backend.api.routes.agent import ingest_telemetry

    for _ in range(3):
        ingest_telemetry(_batch(2), tenant_id="t-1", resource=wired)

    now = int(time.time())
    point = TenantHistoryTable(wired).query_series("t-1", now - 3600, now)[0]
    assert int(point["requests"]) == 6
    assert int(point["batches"]) == 3


def test_a_decision_is_recorded_as_an_episode(wired, monkeypatch):
    """Forced through the real route rather than calling the table directly,
    because the thing being tested is the wiring."""
    from services.backend.api.routes import agent as agent_routes
    from services.backend.ml.model import AnomalyTier

    monkeypatch.setattr(agent_routes, "classify", lambda score, stats: AnomalyTier.HARD_BLOCK)
    ingest = agent_routes.ingest_telemetry

    resp = ingest(_batch(3), tenant_id="t-1", resource=wired)
    assert resp.decisions

    now = int(time.time())
    episodes = TenantHistoryTable(wired).query_episodes("t-1", now - 3600, now)
    assert len(episodes) == 1
    assert episodes[0]["ip"] == "203.0.113.7"
    assert int(episodes[0]["tier2_count"]) == 1


def test_repeated_decisions_stay_one_episode(wired, monkeypatch):
    """The reason episodes are hourly. MitigationState.put fires on every
    scoring pass, so five batches against a still-blocked IP is five puts —
    and must still be one episode."""
    from services.backend.api.routes import agent as agent_routes
    from services.backend.ml.model import AnomalyTier

    monkeypatch.setattr(agent_routes, "classify", lambda score, stats: AnomalyTier.RATE_LIMIT)

    # 3 logs, not 2: compute_features_for_ip returns None below the
    # min_requests_threshold, so a 2-log batch produces no decision at all
    # and the test would pass or fail on bucket timing.
    for _ in range(5):
        agent_routes.ingest_telemetry(_batch(3), tenant_id="t-1", resource=wired)

    now = int(time.time())
    episodes = TenantHistoryTable(wired).query_episodes("t-1", now - 3600, now)
    assert len(episodes) == 1
    assert int(episodes[0]["tier1_count"]) == 5


def test_the_rollup_counts_decisions_too(wired, monkeypatch):
    from services.backend.api.routes import agent as agent_routes
    from services.backend.ml.model import AnomalyTier

    monkeypatch.setattr(agent_routes, "classify", lambda score, stats: AnomalyTier.HARD_BLOCK)
    agent_routes.ingest_telemetry(_batch(3), tenant_id="t-1", resource=wired)

    now = int(time.time())
    point = TenantHistoryTable(wired).query_series("t-1", now - 3600, now)[0]
    assert int(point["tier2_decisions"]) == 1
    assert int(point["tier1_decisions"]) == 0


# --- the rule that matters ----------------------------------------------

def test_a_failed_episode_write_does_not_fail_ingest(wired, monkeypatch):
    """An episode write throttling during an attack must not stop the agent
    receiving the decision it needs to enforce."""
    from botocore.exceptions import ClientError

    from services.backend.api.routes import agent as agent_routes
    from services.backend.ml.model import AnomalyTier

    monkeypatch.setattr(agent_routes, "classify", lambda score, stats: AnomalyTier.HARD_BLOCK)

    def boom(*a, **kw):
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}},
                          "UpdateItem")

    monkeypatch.setattr(TenantHistoryTable, "record_decision", boom)

    resp = agent_routes.ingest_telemetry(_batch(3), tenant_id="t-1", resource=wired)

    assert resp.decisions, "the decision must still reach the agent"
    assert MitigationStateTable(wired).get(tenant_id="t-1", ip="203.0.113.7") is not None


def test_a_failed_rollup_write_does_not_fail_ingest(wired, monkeypatch):
    from botocore.exceptions import ClientError

    from services.backend.api.routes import agent as agent_routes

    def boom(*a, **kw):
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}},
                          "UpdateItem")

    monkeypatch.setattr(TenantHistoryTable, "record_traffic", boom)

    resp = agent_routes.ingest_telemetry(_batch(3), tenant_id="t-1", resource=wired)
    assert resp.received == 3


def test_a_missing_history_table_does_not_fail_ingest(wired, monkeypatch):
    """The deploy-ordering case. Terraform creates the table; if the Lambda
    ships first, every batch would 500 without this guard — and the guard is
    what makes the rollout order forgiving instead of load-bearing."""
    from botocore.exceptions import ClientError

    from services.backend.api.routes import agent as agent_routes

    def boom(*a, **kw):
        raise ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "UpdateItem")

    monkeypatch.setattr(TenantHistoryTable, "record_traffic", boom)
    monkeypatch.setattr(TenantHistoryTable, "record_decision", boom)

    assert agent_routes.ingest_telemetry(_batch(2), tenant_id="t-1", resource=wired).received == 2
