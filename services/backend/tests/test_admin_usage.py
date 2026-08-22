from fastapi.testclient import TestClient

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import create_all_tables
from services.backend.core.usage import get_usage_report
from services.backend.main import app


def _client(dynamo_resource):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app)


def test_usage_middleware_increments_on_every_request(dynamo_resource):
    client = _client(dynamo_resource)
    client.get("/health")
    client.get("/health")
    report = get_usage_report(dynamo_resource, date=None)
    assert report.total_requests == 2


def test_admin_usage_endpoint_reflects_traffic(dynamo_resource):
    client = _client(dynamo_resource)
    client.get("/health")
    resp = client.get("/admin/v1/usage")
    assert resp.status_code == 200
    body = resp.json()
    # The middleware increments AFTER call_next() returns (Step: wire into
    # main.py), so a request's own count isn't visible in its own response
    # — this call sees only the prior /health request's increment (1), not
    # itself. A one-request lag on an approximate ceiling-warning report is
    # harmless; verified here so it's a documented behavior, not a
    # surprise.
    assert body["total_requests"] == 1
    assert "ceiling_warning" in body
