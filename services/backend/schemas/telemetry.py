from pydantic import BaseModel, Field, field_validator

from services.backend.schemas.mitigation import MitigationState


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
    # Each distinct IP in a batch fans out into its own DynamoDB write plus
    # a GSI mirror, so an unbounded batch is unbounded write amplification on
    # the one path the product cannot afford to lose. dependencies.py already
    # claimed "batches carry up to 100 log lines" and used it to justify
    # amortising a read; nothing enforced it. A 422 is strictly better than
    # a fan-out that throttles ingest for every tenant.
    logs: list[LogRecord] = Field(max_length=1000)


class TelemetryResponse(BaseModel):
    received: int
    processed_ips: int
    decisions: list[MitigationState] = []
    # Every address currently in force for this tenant, not only the ones
    # this batch touched. The agent reconciles its local enforcement against
    # this: a blocked IP stops sending traffic, so `decisions` alone would
    # tell the agent to release every attacker.
    #
    # A list of strings rather than full MitigationState objects. Fifty of
    # those, each carrying a seven-float vector, is ~10KB every five seconds
    # — about 172MB/day of egress for what is a set membership test.
    active_ips: list[str] = []
