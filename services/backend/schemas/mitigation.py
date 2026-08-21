from pydantic import BaseModel


class MitigationState(BaseModel):
    ip: str
    tier: int
    score: float
    reason: str
    expires_at: int
