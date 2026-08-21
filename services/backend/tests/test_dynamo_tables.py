import pytest

from services.backend.core.tables import (
    create_all_tables, TenantsTable, AgentsTable, TelemetryEventsTable,
)


def test_create_all_tables_is_idempotent(dynamo_resource):
    create_all_tables(dynamo_resource)
    create_all_tables(dynamo_resource)  # must not raise on second call
    existing = [t.name for t in dynamo_resource.tables.all()]
    assert "Tenants" in existing
    assert "Agents" in existing
    assert "Whitelist" in existing
    assert "MitigationState" in existing
    assert "Models" in existing
    assert "TelemetryEvents" in existing
    assert "UsageCounters" in existing


def test_tenants_put_and_get(dynamo_resource):
    create_all_tables(dynamo_resource)
    table = TenantsTable(dynamo_resource)
    table.put(tenant_id="t-1", name="Acme", contact_email="a@acme.test",
              created_at="2026-08-21T00:00:00Z", status="active")
    item = table.get(tenant_id="t-1")
    assert item["name"] == "Acme"
    assert item["status"] == "active"


def test_tenants_get_missing_returns_none(dynamo_resource):
    create_all_tables(dynamo_resource)
    table = TenantsTable(dynamo_resource)
    assert table.get(tenant_id="does-not-exist") is None


def test_query_by_tenant_uses_correct_partition_key(dynamo_resource):
    create_all_tables(dynamo_resource)
    table = AgentsTable(dynamo_resource)
    table.put(tenant_id="t-1", agent_id="a-1", status="active")
    table.put(tenant_id="t-1", agent_id="a-2", status="active")
    table.put(tenant_id="t-2", agent_id="a-3", status="active")

    results = table.query_by_tenant("t-1")
    assert {r["agent_id"] for r in results} == {"a-1", "a-2"}


def test_telemetry_events_query_by_tenant_raises_not_implemented(dynamo_resource):
    # Partition key here is a "{tenant_id}#{ip}" composite, not a plain
    # tenant_id — must fail loudly, not silently return an empty/wrong
    # result via the inherited base implementation.
    create_all_tables(dynamo_resource)
    table = TelemetryEventsTable(dynamo_resource)
    with pytest.raises(NotImplementedError):
        table.query_by_tenant("t-1")
