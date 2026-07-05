import ipaddress
from pydantic import BaseModel, field_validator


class WhitelistRequest(BaseModel):
    ip: str
    reason: str = ""

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address: {v}")
        return v
