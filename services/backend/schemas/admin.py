from pydantic import BaseModel


class Tenant(BaseModel):
    tenant_id: str
    name: str
    status: str
    created_at: str


class AgentSummary(BaseModel):
    tenant_id: str
    agent_id: str
    agent_version: str | None = None
    status: str
    last_seen_at: str
