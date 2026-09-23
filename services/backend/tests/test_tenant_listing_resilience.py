"""One incomplete tenant row must not take down the Control Platform.

Found by loading `/admin/ui` against production with a real admin session —
something no test and no smoke check had ever done, because
`scripts/smoke-test.sh` is unauthenticated by design (ADR-005 says so, and
says two real bugs shipped past it for that reason).

    pydantic_core.ValidationError: 1 validation error for Tenant
    name
      Field required [type=missing,
                      input_value={'note': 'end-to-end prod...mo',
                                   'status': 'active'}]

`Tenant.name` was declared required. Nothing enforces it: there is no
create-tenant route anywhere in the product, so every tenant that exists was
written straight to DynamoDB, and the one in production carries a `note`
instead of a `name`. The model asserted a guarantee the system never made,
and a single such row 500'd every page that lists tenants — which is both
admin pages.

Two separate faults, fixed separately:

  1. `name` is optional, because it always was in reality. The UI already
     falls back to the tenant id.
  2. A row that still cannot be parsed is skipped rather than propagated.
     A malformed record should cost the operator that row, not the whole
     console — least of all on the screen they would use to investigate it.
"""
from services.backend.api.routes.admin import list_tenants
from services.backend.core.tables import TenantsTable, create_all_tables


def test_a_tenant_without_a_name_is_listed(dynamo_resource):
    """The exact production row shape."""
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="acme-demo", status="active",
                                      created_at="2026-09-21T00:00:00Z",
                                      note="end-to-end production check")

    tenants = list_tenants(resource=dynamo_resource)
    assert [t.tenant_id for t in tenants] == ["acme-demo"]
    assert tenants[0].name is None


def test_one_unparseable_row_does_not_hide_the_others(dynamo_resource):
    """Resilience, not leniency: the good rows still render. Losing the whole
    Control Platform to one bad record means losing the tool you would use to
    find the bad record."""
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="good", name="Acme", status="active",
                                      created_at="2026-09-21T00:00:00Z")
    # No created_at, and nothing in the product guarantees one either.
    TenantsTable(dynamo_resource).put(tenant_id="broken", status="active")

    listed = {t.tenant_id for t in list_tenants(resource=dynamo_resource)}
    assert "good" in listed


def test_a_named_tenant_still_reports_its_name(dynamo_resource):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at="2026-09-21T00:00:00Z")
    assert list_tenants(resource=dynamo_resource)[0].name == "Acme"


def test_the_control_platform_renders_with_an_unnamed_tenant(dynamo_resource,
                                                             cognito_test_keys):
    """End to end through the page that actually broke."""
    from fastapi.testclient import TestClient

    from services.backend.api.cognito_auth import get_jwks
    from services.backend.core.dynamo import get_dynamo_resource
    from services.backend.main import app
    from services.backend.tests.conftest import sign_test_token

    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    client.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"cognito:groups": ["admin"]})})

    TenantsTable(dynamo_resource).put(tenant_id="acme-demo", status="active",
                                      created_at="2026-09-21T00:00:00Z",
                                      note="end-to-end production check")

    assert client.get("/admin/ui").status_code == 200
    tenants_page = client.get("/admin/ui/tenants")
    assert tenants_page.status_code == 200
    assert "acme-demo" in tenants_page.text
