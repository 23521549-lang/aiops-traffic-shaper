"""The local run harness (scripts/run_local.py).

Carried as open since Phase 6: "No supported way to run the application
locally" - the app needed real DynamoDB and real Cognito, and the temporary
mock harness had been removed. A harness nobody tests is how the last one
rotted, so this one is tested.

Two properties matter more than convenience:

1. Authentication is NOT weakened. The real `_decode_and_verify` runs
   unmodified - signature, audience, issuer, expiry and token_use are all
   genuinely checked. The only thing swapped is where public keys come from:
   a locally generated JWKS instead of Cognito's URL.

2. It can never touch real AWS. The machine it runs on may well hold admin
   credentials for the production account. Every call is intercepted by moto,
   and the harness ALSO replaces the environment's credentials with fake ones,
   so that if anything ever escaped the mock it would fail to authenticate
   rather than reach production.
"""
import os

import pytest
from fastapi.testclient import TestClient

from scripts import run_local


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "real-admin-profile")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAREALLOOKINGKEY")
    s = run_local.start_session(seed_model=True)
    yield s
    s.stop()


def test_real_aws_credentials_are_gone_while_it_runs(session):
    """The property, not the spelling: moto installs its own fake key on top of
    the harness's, and either is fine. What must be true is that the real
    ones are nowhere in the environment."""
    assert os.environ["AWS_ACCESS_KEY_ID"] != "AKIAREALLOOKINGKEY"
    assert os.environ["AWS_ACCESS_KEY_ID"] in {"testing", "FOOBARKEY"}
    assert "AWS_PROFILE" not in os.environ


def test_real_credentials_do_not_come_back_when_it_stops(monkeypatch):
    """moto restores the environment it found on start(). The harness swaps
    the credentials out BEFORE starting moto, so what gets restored is the
    fake set - the real ones never return for the life of the process."""
    monkeypatch.setenv("AWS_PROFILE", "real-admin-profile")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAREALLOOKINGKEY")
    run_local.start_session(seed_model=False).stop()

    assert os.environ["AWS_ACCESS_KEY_ID"] != "AKIAREALLOOKINGKEY"
    assert "AWS_PROFILE" not in os.environ


def test_the_app_reports_ready(session):
    client = TestClient(session.app)
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"] == {"dynamodb": True, "cognito_config": True}


def test_the_printed_owner_token_passes_real_verification(session):
    client = TestClient(session.app)
    resp = client.get("/dashboard/v1/model/status",
                      headers={"X-Id-Token": session.owner_token})
    assert resp.status_code == 200


def test_the_printed_admin_token_reaches_the_control_platform(session):
    client = TestClient(session.app)
    resp = client.get("/admin/v1/tenants", headers={"X-Id-Token": session.admin_token})
    assert resp.status_code == 200
    assert [t["tenant_id"] for t in resp.json()] == [session.tenant_id]


def test_a_forged_token_is_still_rejected(session):
    """The harness swaps the key SOURCE, not the verification. A token signed
    with some other key must still fail - otherwise "local mode" would be a
    mode in which authentication does not exist."""
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = other.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
    forged = jwt.encode({"token_use": "id", "custom:tenant_id": session.tenant_id},
                        pem, algorithm="RS256", headers={"kid": run_local.LOCAL_KID})
    client = TestClient(session.app)
    resp = client.get("/dashboard/v1/mitigations", headers={"X-Id-Token": forged})
    assert resp.status_code == 401


def test_the_seeded_model_turns_an_attack_into_a_decision(session):
    """A local run that can only show shadow mode demonstrates nothing about
    the product. With a seeded model the full loop - telemetry in, decision
    out - works on a laptop."""
    client = TestClient(session.app)
    logs = [{
        "time_iso8601": "2026-09-21T00:00:00Z", "remote_addr": "198.51.100.66",
        "request_method": "POST", "request_uri": f"/wp-login.php?try={i}",
        "status": "401", "body_bytes_sent": "90", "request_time": "0.003",
        "http_user_agent": f"python-requests/2.{i % 9}",
    } for i in range(150)]
    resp = client.post("/agent/v1/telemetry", json={"logs": logs},
                       headers={"X-Agent-Key": session.agent_key})
    assert resp.status_code == 200
    assert [d["ip"] for d in resp.json()["decisions"]] == ["198.51.100.66"]


def test_refuses_to_run_inside_a_lambda(monkeypatch):
    """This module disables nothing, but it does install a fake key source
    and fake data. It must never execute where AWS_LAMBDA_FUNCTION_NAME is set -
    and it is not in the deployment package at all (only services/ is)."""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "aiops-traffic-shaper-api")
    with pytest.raises(SystemExit):
        run_local.start_session(seed_model=False)
