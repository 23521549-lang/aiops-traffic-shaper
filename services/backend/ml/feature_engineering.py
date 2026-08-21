import time
from dataclasses import dataclass

from services.backend.core.tables import TelemetryEventsTable

FEATURE_NAMES = [
    "request_rate", "error_ratio", "avg_bytes_sent", "avg_request_time",
    "unique_uri_ratio", "user_agent_entropy", "post_ratio",
]


@dataclass
class FeatureVector:
    remote_addr:        str
    request_rate:       float
    error_ratio:        float
    avg_bytes_sent:     float
    avg_request_time:   float
    unique_uri_ratio:   float
    user_agent_entropy: float
    post_ratio:         float
    sample_size:        int

    def to_list(self) -> list[float]:
        return [getattr(self, name) for name in FEATURE_NAMES]

    def to_dict(self) -> dict:
        return {n: getattr(self, n) for n in ["remote_addr", *FEATURE_NAMES, "sample_size"]}


def _bucket_start(bucket_seconds: int, at: float) -> int:
    return int(at // bucket_seconds) * bucket_seconds


def record_batch(resource, tenant_id: str, logs: list, bucket_seconds: int = 5,
                  now: float | None = None) -> set[str]:
    now = now if now is not None else time.time()
    bucket = _bucket_start(bucket_seconds, now)
    table = TelemetryEventsTable(resource)

    # Aggregate the WHOLE batch in memory first, grouped by IP — this is
    # what keeps writes at 1/IP/batch regardless of how many log lines
    # any single IP contributed (a write-per-line loop here would silently
    # recreate the old Redis design's per-line write cost).
    by_ip: dict[str, dict] = {}
    for log in logs:
        agg = by_ip.setdefault(log.remote_addr, {
            "request_count": 0, "error_count": 0, "post_count": 0,
            "total_bytes": 0, "total_time": 0.0,
            "_uris": set(), "_uas": set(),
        })
        agg["request_count"] += 1
        if str(log.status).startswith(("4", "5")):
            agg["error_count"] += 1
        if log.request_method.upper() == "POST":
            agg["post_count"] += 1
        agg["total_bytes"] += int(float(log.body_bytes_sent))
        agg["total_time"] += float(log.request_time)
        agg["_uris"].add(log.request_uri)
        agg["_uas"].add(log.http_user_agent)

    touched: set[str] = set()
    for ip, agg in by_ip.items():
        agg["distinct_uri_count"] = len(agg.pop("_uris"))
        agg["distinct_ua_count"] = len(agg.pop("_uas"))
        table.add_aggregate(f"{tenant_id}#{ip}", bucket, agg)
        touched.add(ip)
    return touched


def compute_features_for_ip(resource, tenant_id: str, ip: str, bucket_seconds: int = 5,
                             min_requests_threshold: int = 3,
                             now: float | None = None) -> "FeatureVector | None":
    """Sliding-window counter: current bucket's full count plus a weighted
    fraction of the previous bucket, weighted by how far `now` is into the
    current bucket. Standard fixed-window-counter-approximates-sliding-window
    technique — closes the boundary-split evasion a single fixed bucket has,
    at the cost of one extra GetItem (still O(1), no scan)."""
    now = now if now is not None else time.time()
    table = TelemetryEventsTable(resource)
    tenant_ip = f"{tenant_id}#{ip}"

    current_start = _bucket_start(bucket_seconds, now)
    previous_start = current_start - bucket_seconds
    elapsed_fraction = (now - current_start) / bucket_seconds  # 0..1

    # One BatchGetItem round trip instead of two sequential GetItem calls —
    # neither bucket depends on the other's result (found during review).
    buckets = table.get_buckets_batch(tenant_ip, [current_start, previous_start])
    current = buckets.get(current_start, {})
    previous = buckets.get(previous_start, {})
    prev_weight = 1.0 - elapsed_fraction

    def _w(field: str, cast=int) -> float:
        return cast(current.get(field, 0)) + prev_weight * cast(previous.get(field, 0))

    total = _w("request_count")
    if total < min_requests_threshold:
        return None

    distinct_uri = _w("distinct_uri_count")
    distinct_ua = _w("distinct_ua_count")

    return FeatureVector(
        remote_addr=ip,
        request_rate=round(total / bucket_seconds, 6),
        error_ratio=round(_w("error_count") / total, 6),
        avg_bytes_sent=round(_w("total_bytes") / total, 6),
        avg_request_time=round(_w("total_time", float) / total, 6),
        unique_uri_ratio=round(min(distinct_uri / total, 1.0), 6),
        # Approximation, not true Shannon entropy — see docs/PLAN.md Stage 2
        # design note: true entropy doesn't merge additively across buckets,
        # and storing a full frequency distribution reintroduces unbounded
        # item growth. distinct_ua/total is a bounded diversity proxy.
        user_agent_entropy=round(min(distinct_ua / total, 1.0), 6),
        post_ratio=round(_w("post_count") / total, 6),
        sample_size=int(total),
    )
