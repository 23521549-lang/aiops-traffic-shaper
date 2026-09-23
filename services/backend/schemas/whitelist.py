import ipaddress

from pydantic import BaseModel, field_validator


class WhitelistRequest(BaseModel):
    ip: str
    reason: str = ""

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        ipaddress.ip_address(v)  # raises ValueError -> FastAPI 422, matches the superseded ai_engine contract (removed Phase 6)
        return v


class WhitelistEntry(BaseModel):
    whitelisted_ips: list[str]
    # Kept alongside the flat id list rather than replacing it: the
    # agent CLI and the JSON API both consume whitelisted_ips.
    entries: list[dict] = []
