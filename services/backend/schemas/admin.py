from pydantic import BaseModel


class Tenant(BaseModel):
    tenant_id: str
    name: str
    status: str
    created_at: str


class AgentSummary(BaseModel):
    tenant_id: str
    agent_id: str
    # Captured at registration and then dropped from this schema, so the
    # console showed a bare uuid for a machine its owner had already named.
    agent_label: str | None = None
    agent_version: str | None = None
    status: str
    last_seen_at: str
