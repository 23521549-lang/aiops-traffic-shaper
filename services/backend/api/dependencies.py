import hashlib

from fastapi import Depends, Header, HTTPException

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


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
    key_hash = hash_api_key(raw_key)
    agents = AgentsTable(resource).query_by_tenant(tenant_id)
    for agent in agents:
        if agent.get("api_key_hash") == key_hash and agent.get("status") == "active":
            return tenant_id
    raise HTTPException(status_code=401, detail="Invalid agent key")
