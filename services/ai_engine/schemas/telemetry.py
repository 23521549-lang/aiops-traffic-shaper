from pydantic import BaseModel, field_validator


class LogRecord(BaseModel):
    time_iso8601: str
    remote_addr: str
    request_method: str
    request_uri: str
    status: str
    body_bytes_sent: str
    request_time: str
    http_user_agent: str

    @field_validator("status", "body_bytes_sent", "request_time", mode="before")
    @classmethod
    def coerce_to_str(cls, v: object) -> str:
        return str(v)


class TelemetryBatch(BaseModel):
    logs: list[LogRecord]


class TelemetryResponse(BaseModel):
    received: int
    processed_ips: int
    message: str
