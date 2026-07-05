from enum import IntEnum
from pydantic import BaseModel, field_validator
import ipaddress


class MitigationTier(IntEnum):
    RATE_LIMIT = 1
    HARD_BLOCK = 2


class MitigationRequest(BaseModel):
    target_ip: str
    reason: str
    tier: MitigationTier = MitigationTier.RATE_LIMIT

    @field_validator("target_ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address: {v}")
        return v


class MitigationResponse(BaseModel):
    target_ip: str
    tier: int
    action: str
    ttl_seconds: int
    whitelisted: bool = False
    message: str


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


class BlocklistEntry(BaseModel):
    ip: str
    tier: int
    ttl_remaining: int
