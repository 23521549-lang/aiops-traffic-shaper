from pydantic import BaseModel, Field, field_validator

from services.backend.schemas.mitigation import MitigationState

# The enforcement backends services/agent/enforcer/ ships an adapter for.
# Declared here rather than imported from the agent package: the backend does
# not depend on the agent, and this is the wire contract between them.
KNOWN_ENFORCERS = frozenset({"nginx", "iptables"})


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
    # What this agent found it can actually write a rule with. An EMPTY list
    # is a real answer and the one that matters: detect_adapters() returning
    # nothing is a legitimate outcome on a machine with no nginx and no
    # iptables, and that agent then reports telemetry forever while enforcing
    # none of the decisions it is sent. Absent means an agent older than this
    # field, which is a third state and not the same as empty.
    enforcers: list[str] | None = None

    @field_validator("enforcers")
    @classmethod
    def validate_enforcers(cls, v: list[str] | None) -> list[str] | None:
        """A closed set, because this is written onto an item and rendered on
        a page. The names come from the adapter classes, never from an
        agent's configuration."""
        if v is None:
            return None
        unknown = [n for n in v if n not in KNOWN_ENFORCERS]
        if unknown:
            raise ValueError(f"unknown enforcement backend: {unknown[0]!r}")
        return v


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
    # False while the customer has paused enforcement. An agent too old to
    # read this field still does the right thing, because `active_ips` comes
    # back empty and its reconcile releases every local rule - which is the
    # whole behaviour. The flag exists so a current agent can say WHY it
    # released them in its log, instead of reporting what looks like the
    # backend having lost every decision.
    enforce: bool = True
