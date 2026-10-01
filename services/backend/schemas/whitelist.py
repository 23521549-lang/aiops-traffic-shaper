import ipaddress

from pydantic import BaseModel, field_validator


class WhitelistRequest(BaseModel):
    ip: str
    reason: str = ""
    # Which line of evidence the appeal was made from, when one of the seven
    # explains it. Reflected into an audit row a dispute may later turn on,
    # so it is checked against the real feature names rather than stored as
    # given - and it is a closed list, which is what keeps that row the fixed
    # size the ledger depends on.
    because: str = ""

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        ipaddress.ip_address(v)  # raises ValueError -> FastAPI 422, matches the superseded ai_engine contract (removed Phase 6)
        return v

    @field_validator("because")
    @classmethod
    def validate_because(cls, v: str) -> str:
        from services.backend.ml.feature_engineering import FEATURE_NAMES

        if v and v not in FEATURE_NAMES:
            raise ValueError("not one of the measured features")
        return v


class WhitelistEntry(BaseModel):
    whitelisted_ips: list[str]
    # Kept alongside the flat id list rather than replacing it: the
    # agent CLI and the JSON API both consume whitelisted_ips.
    entries: list[dict] = []
