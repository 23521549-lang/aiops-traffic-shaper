import hashlib

from fastapi import Depends, Header, HTTPException

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, TenantsTable
from services.backend.core.usage import (
    is_over_daily_ceiling,
    is_tenant_over_quota,
    seconds_until_daily_reset,
)


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def assert_tenant_active(resource, tenant_id: str) -> None:
    """Phase 4 / H3. Fails closed on a MISSING tenant record too, not just a
    suspended one: an agent whose owning tenant does not exist is not a
    tenant this platform should be serving either."""
    tenant = TenantsTable(resource).get(tenant_id=tenant_id)
    if tenant is None or tenant.get("status") == "suspended":
        raise HTTPException(status_code=403, detail="Tenant is not active")


def agent_auth(x_agent_key: str | None = Header(default=None),
               resource=Depends(get_dynamo_resource)) -> str:
    """A plain FastAPI dependency, not a closure factory over a fixed
    `resource` — found during Stage 4 design review: the plan's original
    `agent_auth(resource)` factory pattern closes over whatever `resource`
    object existed at router-build time (module import). A test-only
    `dynamo_resource_override()` that reassigns a module-level global
    afterward has NO effect on that already-captured closure — the route
    keeps talking to the original (real AWS) resource regardless. Using
    `Depends(get_dynamo_resource)` instead resolves the resource at
    REQUEST time through FastAPI's own dependency graph, which tests can
    swap correctly via `app.dependency_overrides[get_dynamo_resource]`."""
    if x_agent_key is None:
        raise HTTPException(status_code=401, detail="Missing X-Agent-Key")

    try:
        tenant_id, raw_key = x_agent_key.split(".", 1)
    except ValueError:
        raise HTTPException(status_code=401, detail="Malformed X-Agent-Key")

    # Found while wiring up Stage 8's /agent/v1/register (the first real
    # caller to send a RAW key rather than a hash a test hardcoded by
    # hand): this comparison used to check `api_key_hash == raw_key`
    # directly — the client's raw key can never equal its own hash, so
    # every legitimately registered agent would have failed auth here.
    # hash_api_key() existed but was never actually called.
    # H3: checked BEFORE the agent lookup — a suspended tenant is refused
    # regardless of how healthy its individual agent records look.
    assert_tenant_active(resource, tenant_id)

    key_hash = hash_api_key(raw_key)
    agents = AgentsTable(resource).query_by_tenant(tenant_id)
    for agent in agents:
        if agent.get("api_key_hash") == key_hash and agent.get("status") == "active":
            return tenant_id
    raise HTTPException(status_code=401, detail="Invalid agent key")


def enforce_usage_ceiling(resource=Depends(get_dynamo_resource)) -> None:
    """PRD US-4 AC3 - the limiter, applied only to the ingest path.

    Telemetry is the sole high-frequency write path in the system, so it is the
    cost driver and it is what gets refused. Reads stay up on purpose:
    `/agent/v1/decisions` keeps serving so agents already enforcing a block do
    not silently go open during an overload, and the dashboard keeps serving so
    the operator can see the overload while it is happening.

    This costs one small GetItem per telemetry batch. Batches carry up to 100
    log lines, so the read is amortised across them - and it is a read, against
    a separate budget from the writes it is protecting."""
    if is_over_daily_ceiling(resource):
        raise HTTPException(
            status_code=429,
            detail=(
                "Daily free-tier ceiling reached - telemetry ingest is paused "
                "until the quota resets. Existing mitigations remain in force."
            ),
            headers={"Retry-After": str(seconds_until_daily_reset())},
        )


def enforce_tenant_quota(tenant_id: str = Depends(agent_auth),
                         resource=Depends(get_dynamo_resource)) -> str:
    """Per-tenant limiter, checked after the global one.

    The global ceiling pools every tenant, so on its own it let one flooding
    tenant refuse ingest for all of them. This bounds each tenant to its share
    of the day, and names the tenant in the refusal so an agent can tell "my
    tenant is over quota" apart from "the platform is over its ceiling".

    Returns the tenant id so the route can reuse it: FastAPI caches
    agent_auth within a request, so depending on it twice costs one lookup.
    A refused request is not counted - refusing must not itself spend quota."""
    if is_tenant_over_quota(resource, tenant_id):
        raise HTTPException(
            status_code=429,
            detail=(
                "This tenant's daily ingest quota is used up - telemetry is paused "
                "for this tenant only until the quota resets. Other tenants are "
                "unaffected, and existing mitigations remain in force."
            ),
            headers={"Retry-After": str(seconds_until_daily_reset())},
        )
    return tenant_id
