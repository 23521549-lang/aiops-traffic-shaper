from dataclasses import dataclass
from datetime import datetime, timezone

from services.backend.core.tables import UsageCountersTable

_DAILY_REQUEST_CEILING = 1_000_000 / 30
_WARNING_RATIO = 0.8


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
