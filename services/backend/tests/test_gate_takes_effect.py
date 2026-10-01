"""A gate you moved is the gate that judges your next request.

The value of record lives on Tenants, because `save_model` rewrites the Models
item every night and would otherwise revert the operator. The nightly retrain
copies it onto the model, and until now that copy was the ONLY path to
enforcement: a customer lowered their gate during an attack and nothing
changed until the next training run, up to twenty-four hours later. The
console said so, which was honest, and it was still the wrong behaviour for a
security control.

It costs nothing to fix. `assert_tenant_active` already reads the Tenants item
on every authenticated agent request - it has to, to refuse a suspended tenant
- so the gate is already on the wire. It was simply thrown away.

The nightly copy stays. It is what `ModelManager`'s cached stats fall back to,
and it is what keeps a tenant's gate correct in every path that has a model
and no request context.
"""
import time

import numpy as np
import pytest

from services.backend.core.tables import (
    AgentsTable, TenantsTable, create_all_tables,
)
from services.backend.ml.model import (
    TIER1_Z, TIER2_Z, AnomalyTier, ModelManager, classify,
)
from services.backend.ml.registry import ScoreStats


def _stats(**over):
    base = dict(mean=-0.05, std=0.01)
    base.update(over)
    return ScoreStats(**base)


# --- the decision point ----------------------------------------------------


def test_a_live_gate_overrides_the_one_baked_into_the_model():
    """The model carries last night's copy. The request carries today's."""
    score = -0.05 + (-4.5 * 0.01)

    assert classify(score, _stats()) is AnomalyTier.RATE_LIMIT
    assert classify(score, _stats(), tier1_z=-5.0, tier2_z=-6.0) is AnomalyTier.NORMAL


def test_no_live_gate_falls_back_to_the_model_copy():
    """Every path without a request context, and every tenant that has not
    moved anything."""
    score = -0.05 + (-4.5 * 0.01)

    assert classify(score, _stats(tier1_z=-5.0, tier2_z=-6.0)) is AnomalyTier.NORMAL


def test_a_partial_override_does_not_silently_move_the_other_gate():
    """A tenant that has set only tier1 keeps the model's tier2. Defaulting
    the missing one to the shipped constant would move a gate the customer
    never touched."""
    score = -0.05 + (-5.5 * 0.01)

    assert classify(score, _stats(tier1_z=-4.0, tier2_z=-6.0),
                    tier1_z=-3.0) is AnomalyTier.RATE_LIMIT


def test_a_degenerate_spread_still_falls_back_to_absolute_thresholds():
    """No usable spread means no z, so there is no gate to move."""
    assert classify(-0.5, _stats(std=0.0), tier1_z=-3.0) is AnomalyTier.HARD_BLOCK


# --- on the wire -----------------------------------------------------------


@pytest.fixture
def wired(dynamo_resource):
    from services.backend.api.dependencies import hash_api_key

    create_all_tables(dynamo_resource)
    now = "2026-09-25T00:00:00Z"
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active", created_at=now)
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01",
        registered_at=now, last_seen_at=now, agent_version="1.4.0",
        api_key_hash=hash_api_key("secret"), status="active")

    class _Anomalous:
        def decision_function(self, X):
            return np.full(len(X), -0.0905)

    ModelManager._cache.clear()
    # Mean -0.05, sd 0.01, so a score of -0.0905 sits at -4.05 sigma: just
    # past the shipped gate at 4.0 and at 3.5, and inside one at 4.5. Not ON
    # the line: classify uses a strict `z < tier1`, so a score exactly at the
    # threshold is NORMAL and would make this fixture test the boundary
    # rather than the override.
    ModelManager._cache["t-1"] = (_Anomalous(),
                                  ScoreStats(mean=-0.05, std=0.01,
                                             tier1_z=TIER1_Z, tier2_z=TIER2_Z))
    yield dynamo_resource
    ModelManager._cache.clear()


def _ingest(resource):
    from fastapi.testclient import TestClient

    from services.backend.core.dynamo import get_dynamo_resource
    from services.backend.main import app

    app.dependency_overrides[get_dynamo_resource] = lambda: resource
    client = TestClient(app, base_url="https://testserver")
    return client.post("/agent/v1/telemetry", json={"logs": [{
        "time_iso8601": "2026-09-25T12:00:00Z", "remote_addr": "203.0.113.9",
        "request_method": "POST", "request_uri": f"/login?{i}", "status": "401",
        "body_bytes_sent": "90", "request_time": "0.003",
        "http_user_agent": "curl"} for i in range(4)]},
        headers={"X-Agent-Key": "t-1.secret"})


def test_raising_the_gate_takes_effect_on_the_next_batch(wired):
    """The case that matters. A customer being false-positived raises their
    gate and the very next batch is judged by it, not by last night's."""
    TenantsTable(wired).set_threshold("t-1", "tier1_z", -4.5)
    TenantsTable(wired).set_threshold("t-1", "tier2_z", -5.5)

    assert _ingest(wired).json()["decisions"] == []


def test_lowering_the_gate_takes_effect_on_the_next_batch(wired):
    """And the other direction, which is the one reached for during an
    attack."""
    TenantsTable(wired).set_threshold("t-1", "tier2_z", -5.5)
    TenantsTable(wired).set_threshold("t-1", "tier1_z", -3.5)

    assert _ingest(wired).json()["decisions"]


def test_a_tenant_that_has_moved_nothing_is_judged_as_before(wired):
    """Every tenant alive today is in this case."""
    assert _ingest(wired).json()["decisions"]


def test_the_live_gate_costs_no_extra_read(wired, monkeypatch):
    """`assert_tenant_active` reads the Tenants item on every authenticated
    agent request already, because it has to refuse a suspended tenant. The
    gate rides on that read or this change is not worth making."""
    reads = []
    real = TenantsTable.get

    def counting(self, **key):
        reads.append(key)
        return real(self, **key)

    monkeypatch.setattr(TenantsTable, "get", counting)
    _ingest(wired)

    assert len(reads) == 1


def test_a_suspended_tenant_is_still_refused(wired):
    """The check that read was there for in the first place."""
    TenantsTable(wired).suspend("t-1")

    assert _ingest(wired).status_code == 403


def test_the_recorded_decision_carries_the_z_it_was_judged_on(wired):
    """The console explains a decision by the distance that caused it. A
    live gate that changed the outcome without changing the recorded figure
    would make the explanation describe a different judgement."""
    TenantsTable(wired).set_threshold("t-1", "tier1_z", -3.5)

    decision = _ingest(wired).json()["decisions"][0]

    assert decision["z"] == pytest.approx(-4.05, abs=0.01)
    assert decision["decided_at"] >= int(time.time()) - 60
