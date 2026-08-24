from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from services.backend.core.tables import UsageCountersTable

_DAILY_REQUEST_CEILING = 1_000_000 / 30
_WARNING_RATIO = 0.8

# PRD US-4 AC3. Warning at 80% was never enough on its own: it protects the
# budget only if somebody happens to be awake and looking. At 100% of the day's
# share the write-heavy ingest path is refused outright.


@dataclass
class UsageReport:
    date: str
    total_requests: int
    estimated_gb_seconds: float
    dynamodb_consumed_rcu: float
    dynamodb_consumed_wcu: float
    ceiling_warning: bool


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_invocation(resource, estimated_gb_seconds: float = 0.0) -> None:
    UsageCountersTable(resource).add_invocation(_today(), estimated_gb_seconds)


def get_usage_report(resource, date: str | None) -> UsageReport:
    d = date or _today()
    item = UsageCountersTable(resource).get(date=d) or {}
    total = int(item.get("total_requests", 0))
    return UsageReport(
        date=d,
        total_requests=total,
        estimated_gb_seconds=float(item.get("estimated_gb_seconds", 0)),
        dynamodb_consumed_rcu=float(item.get("dynamodb_consumed_rcu", 0)),
        dynamodb_consumed_wcu=float(item.get("dynamodb_consumed_wcu", 0)),
        ceiling_warning=total >= _DAILY_REQUEST_CEILING * _WARNING_RATIO,
    )


def seconds_until_daily_reset(now: datetime | None = None) -> int:
    """The counter is keyed by UTC date, so the quota returns at midnight UTC.
    Agents get this as Retry-After — without it they have no basis for a
    backoff and will hammer the endpoint that just refused them."""
    now = now or datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


def is_over_daily_ceiling(resource, date: str | None = None) -> bool:
    return get_usage_report(resource, date).total_requests >= _DAILY_REQUEST_CEILING
