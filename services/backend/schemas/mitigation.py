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
    # The seven feature values this decision was made on. Seven floats on a
    # write that already happens, and they are what lets the console answer
    # "why this source" instead of only "how far out". Recomputing them at
    # render time is impossible: the 5-second bucket they came from has a
    # 25-hour TTL and the traffic itself is gone.
    features: list[float] | None = None
    reason: str
    expires_at: int
    # When this was decided. Principle 1.5: decided and in effect are two
    # states, permanently, and without this the console cannot tell whether
    # the agent has collected a given decision yet - it can only say that the
    # backend wrote one. Zero on rows written before the field existed, which
    # the presenter reads as "cannot tell" rather than as the epoch.
    decided_at: int = 0

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
