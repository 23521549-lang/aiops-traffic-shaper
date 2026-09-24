"""A field that is written, declared, and never carried off the row.

Five times in this rebuild, in five different places:

  * `last_features` and `stats_version` written onto every episode by
    `record_decision` and dropped by `list_history`, so a decision could be
    explained only while it was still active.
  * the thirteen near-threshold bins written by `record_traffic` and dropped
    by `list_series`, so the rotated axis had nothing to draw.
  * `added_by` written from the verified token by `add_whitelist` and dropped
    by `list_whitelist`, so "who let this address in" had no answer.
  * `enforcers` on the agent item, which `AgentSummary` did not declare.
  * `feature_means` and `feature_stds` projected by `get_metadata` and
    dropped by `model_status`, so the one sentence this product exists to be
    able to say had nowhere to be said.

Every one of them passed every test at the time. Unit tests construct the
model by hand and never touch the reader; integration tests assert on what
the screen shows, and the screen showed nothing because the field never
arrived. The defect is invisible from both ends.

So this file tests the SEAM: write through the real writer, read through the
real reader, and assert the value survived. It is deliberately a list, so
adding a field to a stored item is a decision to add a line here too.
"""
import time
from datetime import datetime, timezone

from services.backend.core.tables import (
    AgentsTable, TenantHistoryTable, TenantsTable, WhitelistTable,
    create_all_tables,
)


def _tenant(resource):
    create_all_tables(resource)
    TenantsTable(resource).put(tenant_id="t-1", name="Acme", status="active",
                               created_at="2026-08-21T00:00:00Z")


def test_an_episode_keeps_its_evidence(dynamo_resource):
    from services.backend.api.routes.dashboard import list_history

    _tenant(dynamo_resource)
    now = int(time.time())
    TenantHistoryTable(dynamo_resource).record_decision(
        "t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-6.2,
        hour_start=TenantHistoryTable.hour_of(now), now=now,
        features=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
        stats_version="v20260924000000")

    episode = list_history(since=now - 3600, until=now + 3600,
                           tenant_id="t-1", resource=dynamo_resource)[0]

    assert list(episode.last_features) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    assert episode.stats_version == "v20260924000000"


def test_an_hourly_row_keeps_its_bins(dynamo_resource):
    from services.backend.api.routes.dashboard import list_series

    _tenant(dynamo_resource)
    now = int(time.time())
    hour = TenantHistoryTable.hour_of(now)
    TenantHistoryTable(dynamo_resource).record_traffic(
        "t-1", hour, requests=40, bins={"n300": 9, "n525": 2})

    point = next(p for p in list_series(since=now - 3600, until=now + 3600,
                                        tenant_id="t-1",
                                        resource=dynamo_resource)
                 if p.hour_start == hour)

    assert point.near == {"n300": 9, "n525": 2}


def test_an_allowed_entry_keeps_who_allowed_it(dynamo_resource):
    from services.backend.api.routes.dashboard import list_whitelist

    _tenant(dynamo_resource)
    WhitelistTable(dynamo_resource).put(
        tenant_id="t-1", ip="10.0.0.9", added_at="2026-09-01T00:00:00Z",
        reason="our CDN", added_by="ops@example.com")

    entry = list_whitelist(tenant_id="t-1", resource=dynamo_resource).entries[0]

    assert entry["added_by"] == "ops@example.com"
    assert entry["reason"] == "our CDN"


def test_an_agent_keeps_what_it_can_enforce_with(dynamo_resource):
    from services.backend.api.routes.dashboard import list_own_agents

    _tenant(dynamo_resource)
    stamp = datetime.now(timezone.utc).isoformat()
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01",
        registered_at=stamp, last_seen_at=stamp, agent_version="1.4.0",
        api_key_hash="h", status="active", enforcers=["nginx"])

    agent = list_own_agents(tenant_id="t-1", resource=dynamo_resource)[0]

    assert list(agent.enforcers) == ["nginx"]


def test_a_model_keeps_the_baseline_it_was_trained_on(dynamo_resource):
    import numpy as np

    from services.backend.api.routes.dashboard import model_status
    from services.backend.ml.training import train_and_save

    _tenant(dynamo_resource)
    rng = np.random.default_rng(43)
    vectors = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
                float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
                float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
                float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(dynamo_resource, "t-1", vectors, stage="production",
                   excluded_whitelist=5)

    status = model_status(tenant_id="t-1", resource=dynamo_resource)

    assert len(status.feature_means) == 7
    assert len(status.feature_stds) == 7
    assert len(status.features) == 7


def test_a_model_keeps_the_gates_copied_onto_it(dynamo_resource):
    import numpy as np

    from services.backend.api.routes.dashboard import model_status
    from services.backend.ml.training import train_and_save

    _tenant(dynamo_resource)
    TenantsTable(dynamo_resource).set_threshold("t-1", "tier1_z", -4.5)
    rng = np.random.default_rng(47)
    vectors = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
                float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
                float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
                float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(dynamo_resource, "t-1", vectors, stage="production")

    assert model_status(tenant_id="t-1",
                        resource=dynamo_resource).tier1_z == -4.5


def test_a_decision_keeps_when_it_was_taken(dynamo_resource):
    from services.backend.api.routes.dashboard import list_mitigations
    from services.backend.core.tables import MitigationStateTable

    _tenant(dynamo_resource)
    now = int(time.time())
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-6.2,
        reason="behavioral_anomaly", expires_at=now + 3600, decided_at=now,
        features=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

    state = list_mitigations(tenant_id="t-1", resource=dynamo_resource)[0]

    assert state.decided_at == now
    assert list(state.features) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
