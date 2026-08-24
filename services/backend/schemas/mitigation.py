import ipaddress

from pydantic import BaseModel, field_validator


class MitigationState(BaseModel):
    ip: str
    tier: int
    score: float
    reason: str
    expires_at: int

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        """Phase 4 / H4. This used to be a bare `str`. The agent writes this
        value into an nginx config file and then reloads nginx, so a value
        containing a newline is an nginx-directive injection on the customer's
        own machine. WhitelistRequest validated its IP from the start; this,
        the field that actually reaches an enforcement backend, did not."""
        ipaddress.ip_address(v)  # raises ValueError -> pydantic ValidationError
        return v
