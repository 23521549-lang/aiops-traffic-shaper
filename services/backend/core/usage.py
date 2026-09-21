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


# --- Per-tenant ingest quota -------------------------------------------------
#
# The global ceiling above protects the bill and nothing else: it pools every
# tenant into one number, so a single tenant flooding - or an attacker holding
# one tenant's agent key - refused ingest for EVERY tenant at once. One noisy
# customer paused protection for all the quiet ones.
#
# The per-tenant quota bounds how much of the shared daily budget any one
# tenant can take. 25% is a deliberate choice for a product with few tenants:
# generous enough that a legitimately busy tenant is not throttled early, small
# enough that no tenant can leave the others with nothing. The global ceiling
# stays underneath it as the backstop for the bill - both are checked.
_TENANT_DAILY_SHARE = 0.25

# Tenant rows live in the same UsageCounters table as the global row, keyed on
# the same `date` attribute. The marker makes collision impossible: the global
# key is a bare date, and no tenant id can make "YYYY-MM-DD#tenant#..." equal to
# one. A tenant literally named "2026-09-21" still lands on its own row.
_TENANT_KEY_MARKER = "#tenant#"


def tenant_daily_quota() -> int:
    return int(_DAILY_REQUEST_CEILING * _TENANT_DAILY_SHARE)


def _tenant_counter_key(tenant_id: str, date: str | None = None) -> str:
    return f"{date or _today()}{_TENANT_KEY_MARKER}{tenant_id}"


def record_tenant_ingest(resource, tenant_id: str, count: int = 1) -> None:
    """One atomic ADD per accepted telemetry BATCH - never per log line, so
    this adds a constant one write per batch and the cost model's central
    property (writes do not grow with log volume) is untouched."""
    UsageCountersTable(resource).update(
        key={"date": _tenant_counter_key(tenant_id)},
        update_expression="ADD total_requests :n",
        expr_values={":n": count},
    )


def tenant_requests_today(resource, tenant_id: str) -> int:
    item = UsageCountersTable(resource).get(date=_tenant_counter_key(tenant_id)) or {}
    return int(item.get("total_requests", 0))


def is_tenant_over_quota(resource, tenant_id: str) -> bool:
    return tenant_requests_today(resource, tenant_id) >= tenant_daily_quota()
