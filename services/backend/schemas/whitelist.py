import ipaddress

from pydantic import BaseModel, field_validator


class WhitelistRequest(BaseModel):
    ip: str
    reason: str = ""

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        ipaddress.ip_address(v)  # raises ValueError -> FastAPI 422, matches the old ai_engine contract
        return v


class WhitelistEntry(BaseModel):
    whitelisted_ips: list[str]
