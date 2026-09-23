"""Whitelisting an IP has to actually unblock it.

The defect, found in the portal review and confirmed against the shipped
code: `add_whitelist` wrote a row to the Whitelist table and nothing else.
The whitelist was consulted only at SCORING time (`api/routes/agent.py:99`),
so it prevented the NEXT decision — it did not undo the one already in force.

What the customer experienced:

    1. A partner's crawler shows up in "Active mitigations", hard-blocked.
    2. They click Add to whitelist. The server answers
       "203.0.113.4 added to whitelist". Success.
    3. The agent polls /agent/v1/decisions, which returned MitigationState
       unfiltered, and keeps the nginx `deny` in place — for up to an hour
       on a tier-2 block.
    4. The dashboard keeps listing it as active, with no explanation.

The whitelist is the only lever this product hands a customer for "you got
this one wrong". It reported success and did nothing they could observe.
"""
import time

from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.api.routes.dashboard import add_whitelist
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import MitigationStateTable, create_all_tables
from services.backend.main import app
from services.backend.schemas.whitelist import WhitelistRequest
from services.backend.tests.conftest import sign_test_token


def _future() -> int:
    return int(time.time()) + 3600


def _block(resource, tenant_id: str, ip: str) -> None:
    MitigationStateTable(resource).put(
        tenant_id=tenant_id, ip=ip, tier=2, score=-0.2, z=-5.4,
        reason="behavioral_anomaly", expires_at=_future(),
    )


def _client(dynamo_resource, cognito_test_keys, tenant_id="t-1"):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": tenant_id})
    client.post("/ui/login", data={"id_token": token})
    return client


def test_whitelisting_an_ip_clears_its_active_mitigation(dynamo_resource):
    create_all_tables(dynamo_resource)
    _block(dynamo_resource, "t-1", "203.0.113.4")

    add_whitelist(WhitelistRequest(ip="203.0.113.4", reason="partner crawler"),
                  tenant_id="t-1", resource=dynamo_resource)

    assert MitigationStateTable(dynamo_resource).get(tenant_id="t-1", ip="203.0.113.4") is None


def test_the_agent_stops_being_told_to_block_a_whitelisted_ip(dynamo_resource):
    """The agent's view is the one that matters — it is what writes the nginx
    deny. Clearing the row is what makes the next poll return a shorter list."""
    from services.backend.api.routes.agent import list_decisions

    create_all_tables(dynamo_resource)
    _block(dynamo_resource, "t-1", "203.0.113.4")
    _block(dynamo_resource, "t-1", "198.51.100.7")

    add_whitelist(WhitelistRequest(ip="203.0.113.4", reason=""),
                  tenant_id="t-1", resource=dynamo_resource)

    served = {d.ip for d in list_decisions(tenant_id="t-1", resource=dynamo_resource)}
    assert served == {"198.51.100.7"}


def test_whitelisting_an_ip_that_was_never_blocked_is_not_an_error(dynamo_resource):
    """The common case: pre-approving an uptime monitor before it ever trips
    the detector. Deleting a key that is not there must not raise."""
    create_all_tables(dynamo_resource)
    add_whitelist(WhitelistRequest(ip="203.0.113.9", reason="uptime monitor"),
                  tenant_id="t-1", resource=dynamo_resource)
    assert MitigationStateTable(dynamo_resource).get(tenant_id="t-1", ip="203.0.113.9") is None


def test_clearing_is_scoped_to_the_whitelisting_tenant(dynamo_resource):
    """The tenant-isolation guarantee, on a newly-added write path. t-1
    whitelisting an IP must not disturb t-2's block on the same IP — two
    tenants can legitimately see the same address behave differently."""
    create_all_tables(dynamo_resource)
    _block(dynamo_resource, "t-1", "203.0.113.4")
    _block(dynamo_resource, "t-2", "203.0.113.4")

    add_whitelist(WhitelistRequest(ip="203.0.113.4", reason=""),
                  tenant_id="t-1", resource=dynamo_resource)

    assert MitigationStateTable(dynamo_resource).get(tenant_id="t-1", ip="203.0.113.4") is None
    assert MitigationStateTable(dynamo_resource).get(tenant_id="t-2", ip="203.0.113.4") is not None


def test_an_invalid_ip_clears_nothing(dynamo_resource):
    """`add_whitelist` is reached through a pydantic-validated request, so a
    malformed address raises before any write. Guard against a future
    refactor that clears the mitigation first and validates second."""
    import pytest
    from pydantic import ValidationError

    create_all_tables(dynamo_resource)
    _block(dynamo_resource, "t-1", "203.0.113.4")

    with pytest.raises(ValidationError):
        add_whitelist(WhitelistRequest(ip="203.0.113.999", reason=""),
                      tenant_id="t-1", resource=dynamo_resource)

    assert MitigationStateTable(dynamo_resource).get(tenant_id="t-1", ip="203.0.113.4") is not None


def test_the_dashboard_stops_listing_it_immediately(dynamo_resource, cognito_test_keys):
    """End to end through the UI route the customer actually clicks, because
    the visible symptom was 'I clicked Allow and it is still listed'."""
    client = _client(dynamo_resource, cognito_test_keys, "t-1")
    _block(dynamo_resource, "t-1", "203.0.113.4")
    # The tier badge renders only inside the mitigation table, so it is a
    # precise marker for "this IP is listed as blocked". The address itself
    # is not: after whitelisting it legitimately appears several times in the
    # whitelist table (cell, hx-delete URL, hx-confirm text).
    assert "Blocked" in client.get("/dashboard/ui").text

    resp = client.post("/dashboard/ui/whitelist?ip=203.0.113.4",
                       headers={"X-CSRF-Token": client.cookies["csrf_token"]})
    assert resp.status_code == 200

    page = client.get("/dashboard/ui").text
    assert "Blocked" not in page
    assert client.get("/dashboard/ui/whitelist").text.count("203.0.113.4") >= 1
