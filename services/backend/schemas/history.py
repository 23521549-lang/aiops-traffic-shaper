from pydantic import BaseModel, Field


class MitigationEpisode(BaseModel):
    """One IP, one hour, however many decisions that took.

    Not one row per decision: `MitigationState.put` fires on every scoring
    pass for an already-blocked IP and the agent flushes every five seconds,
    so per-decision rows would reach ~720 per attacking IP per hour. The
    hourly episode is both far cheaper and the better artefact — "17
    decisions, escalated to a block" is what a person wants to read.
    """

    ip: str
    hour_start: int
    first_ts: int
    last_ts: int
    tier1_count: int = 0
    tier2_count: int = 0
    last_score: float | None = None
    # None where the model had no usable spread, exactly as on
    # MitigationState. Rendering 0.0 would be a lie about the decision taken.
    last_z: float | None = None
    reason: str = "behavioral_anomaly"
    is_new: bool = False
    # The vector that justified the decision, and the id of the statistics it
    # was measured against. Optional because episodes written before these
    # existed have neither, and a page must render those rather than 500 -
    # the same lesson Tenant.name taught on real production data.
    last_features: list[float] = Field(default_factory=list)
    stats_version: str | None = None

    @property
    def max_tier(self) -> int:
        return 2 if self.tier2_count else 1

    @property
    def decisions(self) -> int:
        return self.tier1_count + self.tier2_count


class HourlyPoint(BaseModel):
    """One hour of traffic. Zero-filled server-side for hours with no row —
    DynamoDB has nothing to write when nothing happened, and a chart that
    skips those hours draws a flat line across an outage instead of a
    hole."""

    hour_start: int
    requests: int = 0
    batches: int = 0
    tier1_decisions: int = 0
    tier2_decisions: int = 0
