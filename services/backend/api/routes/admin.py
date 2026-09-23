import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from botocore.exceptions import ClientError
from pydantic import ValidationError

from services.backend.api.cognito_auth import admin_auth
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.ui.csrf import verify_csrf_if_cookie_auth
from services.backend.core.tables import AgentsTable, TelemetryEventsTable, TenantsTable
from services.backend.api.cognito_login import get_cognito_client
from services.backend.core.config import settings
from services.backend.schemas.admin import (
    AgentSummary, Tenant, TenantCreateRequest, TenantCreateResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/v1/tenants", response_model=list[Tenant], dependencies=[Depends(admin_auth)])
def list_tenants(resource=Depends(get_dynamo_resource)) -> list[Tenant]:
    # Skip rather than propagate. A record this cannot parse costs the
    # operator that one row; raising costs them the entire Control Platform,
    # including the screen they would use to find the bad record.
    tenants = []
    for item in TenantsTable(resource).list_all():
        try:
            tenants.append(Tenant(**item))
        except ValidationError:
            logger.warning("Skipping unparseable tenant row: %s", item.get("tenant_id"))
    return tenants


@router.get("/admin/v1/agents", response_model=list[AgentSummary], dependencies=[Depends(admin_auth)])
def list_agents(status: str = "active", resource=Depends(get_dynamo_resource),
                now: datetime | None = None) -> list[AgentSummary]:
    """Cross-tenant fleet health, one GSI query per call, no scan.

    `status` is a liveness word, not the stored attribute. "active" and
    "stale" both mean lifecycle-active and are told apart by `last_seen_at`
    against the LastSeenIndex sort key; only "revoked" is a stored state.
    The default used to be "stale", which matched nothing ever written, so
    the landing view was permanently empty — see test_agent_liveness.py.
    """
    table = AgentsTable(resource)
    if status == "revoked":
        items = table.query_revoked()
    elif status == "stale":
        items = table.query_stale(now=now)
    else:
        items = table.query_live(now=now)
    return [AgentSummary(**item) for item in items]


@router.post("/admin/v1/tenants/{tenant_id}/suspend",
             dependencies=[Depends(admin_auth), Depends(verify_csrf_if_cookie_auth)])
def suspend_tenant(tenant_id: str, resource=Depends(get_dynamo_resource)) -> dict:
    if not TenantsTable(resource).suspend(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    # Phase 4 / H3: suspension used to flip one attribute nobody read. It now
    # also revokes every API key already issued to this tenant's agents —
    # otherwise those keys keep working, since agent keys have no expiry.
    revoked = AgentsTable(resource).revoke_all_for_tenant(tenant_id)
    return {"message": f"tenant {tenant_id} suspended", "agents_revoked": revoked}


@router.post("/admin/v1/tenants/{tenant_id}/reactivate",
             dependencies=[Depends(admin_auth), Depends(verify_csrf_if_cookie_auth)])
def reactivate_tenant(tenant_id: str, resource=Depends(get_dynamo_resource)) -> dict:
    """Undo a suspension. Until this existed suspension was one-way, short of
    editing DynamoDB by hand.

    The tenant comes back; its old agent keys do not. suspend_tenant revoked
    them because the reason for suspending may be a leaked key, so the tenant
    registers fresh agents to get fresh keys. The response says so, because an
    operator reactivating a tenant will otherwise reasonably expect its agents
    to start reporting again on their own - and they will not."""
    if not TenantsTable(resource).reactivate(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {
        "message": f"tenant {tenant_id} reactivated",
        "note": "agent keys revoked at suspension stay revoked; register agents again",
    }


@router.post("/admin/v1/tenants/{tenant_id}/training-exclude/{ip}",
             dependencies=[Depends(admin_auth), Depends(verify_csrf_if_cookie_auth)])
def exclude_ip_from_training(tenant_id: str, ip: str,
                             resource=Depends(get_dynamo_resource)) -> dict:
    """Stop the nightly retrain learning from this IP's traffic.

    Flagging at decision time keeps an attacker from teaching the model, but
    it cannot undo a model that has already learned them: a poisoned model
    stops detecting the attack, so it stops flagging it, and the next retrain
    learns it again. Observed on production - an attack scoring -0.204 drew no
    decision at all two days later. This is the way out.

    The inverse of the whitelist, and deliberately not the same thing: the
    whitelist exempts an IP from MITIGATION, this exempts one from TRAINING.
    The IP is still scored and still blocked."""
    excluded = TelemetryEventsTable(resource).flag_all_for_ip(tenant_id, ip)
    return {
        "message": f"{ip} excluded from training for tenant {tenant_id}",
        "buckets_excluded": excluded,
        "note": "retrain to rebuild the model without this traffic",
    }


# --- tenant creation ------------------------------------------------------

@router.post("/admin/v1/tenants", response_model=TenantCreateResponse, status_code=201,
             dependencies=[Depends(admin_auth), Depends(verify_csrf_if_cookie_auth)])
def create_tenant(body: TenantCreateRequest,
                  resource=Depends(get_dynamo_resource),
                  cognito=Depends(get_cognito_client)) -> TenantCreateResponse:
    """The route that did not exist.

    Every tenant in this system was written straight into DynamoDB by hand,
    which is why the one in production has a `note` and no `name` and 500'd
    both admin pages on real data.

    Ordering is deliberate: **DynamoDB first, Cognito second, `provisioning`
    in between.** Cognito-first would leave a user whose `custom:tenant_id`
    points at nothing — and `assert_tenant_active` fails closed on a missing
    tenant, so that user would be silently inert with no row anywhere for an
    operator to find. This way a failure leaves a visible `provisioning` row
    that a retry of the same request can finish.

    `custom:tenant_id` is `mutable = false` and absent from the client's
    write_attributes (terraform/cognito.tf), so **creation is the only
    moment it can ever be set**. There is no repair path for a user created
    without it, only delete-and-recreate — which is why the irreversible
    step comes after the cheap retryable one.
    """
    tenant_id = (body.tenant_id or uuid.uuid4().hex).strip()
    now = datetime.now(timezone.utc).isoformat()
    table = TenantsTable(resource)

    created = table.create(tenant_id, name=body.name,
                           contact_email=body.contact_email,
                           status="provisioning", created_at=now)
    if not created:
        existing = table.get(tenant_id=tenant_id) or {}
        if existing.get("status") != "provisioning":
            raise HTTPException(status_code=409, detail=f"Tenant {tenant_id} already exists")
        # A retry of an attempt that died between the two writes. Carry on
        # and let AdminCreateUser be the idempotent step.

    try:
        cognito.admin_create_user(
            UserPoolId=settings.cognito_user_pool_id,
            Username=body.contact_email,
            UserAttributes=[
                {"Name": "email", "Value": body.contact_email},
                # The pool sets account recovery to verified_email but never
                # verifies anyone's, so password recovery cannot work for a
                # user created without this.
                {"Name": "email_verified", "Value": "true"},
                {"Name": "custom:tenant_id", "Value": tenant_id},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code != "UsernameExistsException":
            logger.error("tenant %s left provisioning: cognito said %s", tenant_id, code)
            raise HTTPException(
                status_code=502,
                detail=(f"Tenant {tenant_id} was created but its first user was not. "
                        "Retry this request to finish it.")) from None

    table.update(
        key={"tenant_id": tenant_id},
        update_expression="SET #s = :active",
        expr_names={"#s": "status"},
        expr_values={":active": "active"},
        condition_expression="attribute_exists(tenant_id)",
    )

    return TenantCreateResponse(
        tenant_id=tenant_id, name=body.name, status="active", created_at=now,
        first_user_email=body.contact_email,
        # The temporary password is emailed by Cognito and never touches
        # this backend's logs or its response body.
        password_delivery="cognito_email",
    )
