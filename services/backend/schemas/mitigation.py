import ipaddress

from pydantic import BaseModel, field_validator


class MitigationState(BaseModel):
    ip: str
    tier: int
    score: float
    # Standard deviations below this tenant's own normal, recorded at decision
    # time. ADR-006: the raw `score` has no stable meaning across models — the
    # same attack scored -0.204 and -0.092 against two models of one tenant —
    # so the raw number alone cannot be shown to a customer. Recomputing z at
    # render time would be wrong too, because the nightly retrain moves the
    # statistics it is derived from. None where the model had no usable
    # spread, and for rows written before this field existed.
    z: float | None = None
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
