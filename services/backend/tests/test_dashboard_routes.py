from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import MitigationStateTable, ModelsTable, create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


def _client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    return TestClient(app)


def _tenant_headers(cognito_test_keys, tenant_id="t-1"):
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": tenant_id})
    return {"Authorization": f"Bearer {token}"}


def test_dashboard_requires_auth(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/dashboard/v1/mitigations")
    assert resp.status_code == 401


def test_dashboard_mitigations_scoped_to_tenant(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="1.1.1.1", tier=2, score=-0.5, reason="x", expires_at=0,
    )
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-2", ip="2.2.2.2", tier=2, score=-0.5, reason="x", expires_at=0,
    )
    resp = client.get("/dashboard/v1/mitigations", headers=_tenant_headers(cognito_test_keys, "t-1"))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["ip"] == "1.1.1.1"


def test_whitelist_add_list_remove_roundtrip(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    headers = _tenant_headers(cognito_test_keys)

    resp = client.post("/dashboard/v1/whitelist", json={"ip": "9.9.9.9", "reason": "known-good"},
                        headers=headers)
    assert resp.status_code == 200

    resp = client.get("/dashboard/v1/whitelist", headers=headers)
    assert resp.json()["whitelisted_ips"] == ["9.9.9.9"]

    resp = client.delete("/dashboard/v1/whitelist/9.9.9.9", headers=headers)
    assert resp.status_code == 200

    resp = client.get("/dashboard/v1/whitelist", headers=headers)
    assert resp.json()["whitelisted_ips"] == []


def test_whitelist_invalid_ip_is_422(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.post("/dashboard/v1/whitelist", json={"ip": "not-an-ip"},
                        headers=_tenant_headers(cognito_test_keys))
    assert resp.status_code == 422


def test_whitelist_remove_missing_ip_is_404(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.delete("/dashboard/v1/whitelist/1.2.3.4", headers=_tenant_headers(cognito_test_keys))
    assert resp.status_code == 404


def test_model_status_no_model_is_shadow_mode(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/dashboard/v1/model/status", headers=_tenant_headers(cognito_test_keys))
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_ready"] is False
    assert body["shadow_mode"] is True


def test_model_status_with_model(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)  # creates tables first
    ModelsTable(dynamo_resource).put(
        tenant_id="t-1", stage_version="production", model_blob=b"fake",
        version="v1", trained_at="2026-08-21T00:00:00Z", training_samples=200,
        contamination=0.05, score_mean=0.0, score_std=1.0, features=["request_rate"],
        stage="production",
    )
    resp = client.get("/dashboard/v1/model/status", headers=_tenant_headers(cognito_test_keys))
    body = resp.json()
    assert body["model_ready"] is True
    assert body["version"] == "v1"


def test_mitigation_state_rejects_a_non_ip_value():
    """Phase 4 / H4, backend half: MitigationState.ip was a bare `str`, so the
    backend would happily persist and serve a value the agent then writes into
    an nginx config. WhitelistRequest already validated its IP; this did not."""
    import pytest
    from pydantic import ValidationError

    from services.backend.schemas.mitigation import MitigationState
    with pytest.raises(ValidationError):
        MitigationState(ip="1.2.3.4\ndeny all;", tier=2, score=-0.5,
                        reason="behavioral_anomaly", expires_at=0)
