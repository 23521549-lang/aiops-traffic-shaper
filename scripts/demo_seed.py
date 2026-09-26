"""Realistic data for looking at the portal.

`run_local.py --demo` calls this. It exists because an empty portal cannot
be judged: every screen in this product has an empty state, a populated
state and a degraded state, and only one of those shows up on a fresh
install.

Nothing here touches AWS — run_local.py has already replaced boto3's
backend with an in-process moto mock and neutralised any real credentials
before this module is imported.

The numbers are taken from real measurements rather than invented:
the -0.204 / -5.9σ and -0.092 / -4.21σ pairs are the two production models
from ADR-006 scoring the same brute-force attack, which is the case that
motivated sigma-based tiering in the first place.
"""
import secrets
import time
from datetime import datetime, timedelta, timezone

from services.backend.api.dependencies import hash_api_key
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantHistoryTable, TenantsTable,
    UsageCountersTable, WhitelistTable,
)
from services.backend.core.usage import _DAILY_REQUEST_CEILING, _TENANT_KEY_MARKER, _today


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def seed(resource, primary_tenant: str) -> dict:
    now = datetime.now(timezone.utc)
    epoch = int(time.time())
    out = {"agent_keys": {}}

    # --- tenants, in every lifecycle state -------------------------------
    tenants = [
        (primary_tenant, "Acme Storefront", "active", 21),
        ("globex-docs", "Globex Documentation", "active", 9),
        ("initech-api", "Initech Public API", "active", 40),
        ("umbrella-cdn", "Umbrella CDN", "suspended", 64),
        # No `name` — the exact shape that 500'd the Control Platform in
        # production, kept here so that path stays exercised by eye too.
        ("hooli-legacy", None, "active", 120),
    ]
    for tenant_id, name, status, age_days in tenants:
        fields = {"tenant_id": tenant_id, "status": status,
                  "created_at": _iso(now - timedelta(days=age_days))}
        if name:
            fields["name"] = name
        else:
            fields["note"] = "imported before the console existed"
        TenantsTable(resource).put(**fields)

    # --- agents: live, borderline, long gone, revoked --------------------
    agents = [
        (primary_tenant, "a-7f3c2b", "web-01 (nginx)", "1.4.0", "active", timedelta(seconds=12)),
        (primary_tenant, "a-91ad04", "web-02 (nginx)", "1.4.0", "active", timedelta(seconds=48)),
        (primary_tenant, "a-3ce881", "staging-01", "1.3.2", "active", timedelta(hours=9)),
        ("globex-docs", "a-55bb10", "docs-edge", "1.4.0", "active", timedelta(minutes=2)),
        ("initech-api", "a-2d7e6f", "api-gw-01", "1.2.0", "active", timedelta(days=3)),
        ("initech-api", "a-8a44c9", "api-gw-02", "1.2.0", "active", timedelta(minutes=1)),
        ("umbrella-cdn", "a-0f1122", "edge-eu-1", "1.1.0", "revoked", timedelta(days=11)),
        ("hooli-legacy", "a-6b9d3e", "legacy-lb", "0.9.1", "active", timedelta(days=41)),
    ]
    for tenant_id, agent_id, label, version, status, ago in agents:
        raw = secrets.token_urlsafe(24)
        AgentsTable(resource).put(
            tenant_id=tenant_id, agent_id=agent_id, agent_label=label,
            registered_at=_iso(now - timedelta(days=30)),
            last_seen_at=_iso(now - ago),
            agent_version=version, api_key_hash=hash_api_key(raw), status=status,
        )
        if tenant_id == primary_tenant:
            out["agent_keys"][agent_id] = f"{tenant_id}.{raw}"

    # --- mitigations, spanning both tiers and the whole expiry range -----
    # z values chosen to land in each severity band the copy distinguishes,
    # so all four phrasings are visible on one screen.
    # The feature vector is what the "why this source" pane decomposes:
    # request_rate, error_ratio, avg_bytes, avg_time, uri_ratio, ua_entropy,
    # post_ratio. Shaped like the real cases so each row reads as a
    # recognisable attack rather than as noise.
    mitigations = [
        # A login brute force: fast, nearly all errors, nearly all POSTs, one URI.
        ("198.51.100.66", 2, -0.204, -6.42, 3180,
         [8.4, 0.71, 90.0, 0.003, 0.02, 0.90, 0.94]),
        # A scraper: fast and wide, but it gets what it asks for.
        ("203.0.113.201", 2, -0.188, -5.31, 2040,
         [6.9, 0.02, 14200.0, 0.41, 0.97, 0.30, 0.01]),
        # A vulnerability scan: many distinct paths, most of them 404.
        ("192.0.2.144", 1, -0.131, -4.62, 250,
         [3.1, 0.58, 420.0, 0.02, 0.96, 0.40, 0.07]),
        # A misconfigured client retrying one endpoint.
        ("198.51.100.7", 1, -0.092, -4.21, 47,
         [2.8, 0.44, 310.0, 0.01, 0.04, 0.10, 0.62]),
        ("203.0.113.88", 1, -0.115, None, 190,
         [2.2, 0.20, 900.0, 0.08, 0.30, 0.70, 0.18]),
    ]
    for ip, tier, score, z, ttl, features in mitigations:
        # `decided_at` matters as much as any other field here: without it the
        # console correctly answers "we cannot tell whether your agents have
        # this", and a demo that only ever shows the cannot-tell branch never
        # shows what the product can actually do.
        fields = {"tenant_id": primary_tenant, "ip": ip, "tier": tier, "score": score,
                  "reason": "behavioral_anomaly", "expires_at": epoch + ttl,
                  "decided_at": epoch - 420, "features": features}
        if z is not None:
            fields["z"] = z
        MitigationStateTable(resource).put(**fields)

    # A row that has already lapsed. DynamoDB TTL deletes lazily — AWS says
    # up to 48 hours — so this is what the table really looks like, and the
    # portal must not list it as active.
    # No `decided_at` on purpose: this is what a row written before the field
    # existed looks like, and the console has a third state for exactly it.
    MitigationStateTable(resource).put(
        tenant_id=primary_tenant, ip="203.0.113.250", tier=1, score=-0.11, z=-4.3,
        reason="behavioral_anomaly", expires_at=epoch - 900,
    )

    # --- whitelist -------------------------------------------------------
    for ip, reason in [("203.0.113.4", "partner crawler"),
                       ("198.51.100.30", "uptime monitor"),
                       ("192.0.2.9", "load test runner")]:
        WhitelistTable(resource).put(tenant_id=primary_tenant, ip=ip,
                                     added_at=_iso(now - timedelta(days=3)), reason=reason)

    # --- usage: deliberately past the 80% warning line -------------------
    today = _today()
    total = int(_DAILY_REQUEST_CEILING * 0.84)
    UsageCountersTable(resource).put(date=today, total_requests=total,
                                     estimated_gb_seconds=18.42)
    # The primary tenant stays INSIDE its own 25% quota and one other tenant
    # blows through theirs. That is both the more interesting story for the
    # operations console - one customer hogging while the others are fine -
    # and the only arrangement in which the tenant console is worth looking
    # at: over its quota, axis_state correctly freezes the plot and draws no
    # reading at all, which is right and which hides every source on the one
    # screen the demo exists to show.
    for tenant_id, share in [(primary_tenant, 0.17), ("globex-docs", 0.09),
                             ("initech-api", 0.62), ("hooli-legacy", 0.12)]:
        UsageCountersTable(resource).put(
            date=f"{today}{_TENANT_KEY_MARKER}{tenant_id}",
            total_requests=int(total * share),
        )

    # --- history: 24 hours of hourly rollups and a handful of episodes ---
    history = TenantHistoryTable(resource)
    hour_now = TenantHistoryTable.hour_of(epoch)
    # A quiet day with two busy stretches, so the chart has a shape rather
    # than a flat line or a wall.
    shape = [140, 120, 95, 80, 70, 65, 90, 210, 380, 520, 610, 640,
             590, 620, 700, 660, 580, 540, 610, 720, 480, 330, 240, 180]
    # The thirteen near-threshold bins, shaped like a real tail: many
    # readings just past 3 sigma, very few out at 5. Without them the axis has
    # no density below the gate and the gate control answers "0" at every one
    # of its thirteen positions, which is the one part of this product a
    # demo most needs to show.
    tail = {"n300": 34, "n325": 21, "n350": 13, "n375": 8, "n400": 5,
            "n425": 3, "n450": 2, "n475": 1, "n500": 1}
    for i, requests in enumerate(shape):
        hour = hour_now - (23 - i) * 3600
        busy = requests > 400
        # tier1 and tier2 were seeded at zero, which made the three figures
        # at the ends of the traffic lanes read "9.270 / 0 / 0" on a screen
        # that was simultaneously holding five sources. The counts are what
        # `record_traffic` has always written on ingest; the demo simply
        # never wrote them, so the one screen that reads them looked broken.
        history.record_traffic(
            primary_tenant, hour_start=hour, requests=requests,
            tier1=(9 if busy else 2), tier2=(2 if busy else 0),
            bins={k: max(1, v // (2 if busy else 6)) for k, v in tail.items()})

    # Episodes, placed where the traffic is busiest so the chart and the
    # table tell the same story.
    episodes = [
        ("198.51.100.66", 19, 2, -6.42, 11),
        ("198.51.100.66", 20, 2, -5.88, 4),
        ("203.0.113.201", 14, 2, -5.31, 6),
        ("192.0.2.144", 11, 1, -4.62, 3),
        ("198.51.100.7", 9, 1, -4.21, 2),
        ("203.0.113.88", 7, 1, None, 1),
    ]
    for ip, hours_ago, tier, z, count in episodes:
        hour = hour_now - (23 - hours_ago) * 3600
        for n in range(count):
            history.record_decision(
                primary_tenant, ip, hour_start=hour, tier=tier,
                now=hour + 120 + n * 90, score=(z / 30 if z else -0.11), z=z)

    out["tenants"] = [t[0] for t in tenants]
    return out
