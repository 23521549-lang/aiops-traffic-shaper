from pydantic import BaseModel


class AgentRegisterRequest(BaseModel):
    agent_label: str


class AgentRegisterResponse(BaseModel):
    tenant_id: str
    agent_id: str
    api_key: str  # shown once — not retrievable again, only api_key_hash is stored
