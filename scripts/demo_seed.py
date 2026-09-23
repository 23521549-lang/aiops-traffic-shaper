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
    AgentsTable, MitigationStateTable, TenantsTable, UsageCountersTable, WhitelistTable,
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
    mitigations = [
        ("198.51.100.66", 2, -0.204, -6.42, 3180),   # Far outside
        ("203.0.113.201", 2, -0.188, -5.31, 2040),   # Well outside
        ("192.0.2.144", 1, -0.131, -4.62, 250),      # Clearly outside
        ("198.51.100.7", 1, -0.092, -4.21, 47),      # Outside, about to end
        ("203.0.113.88", 1, -0.115, None, 190),      # no usable spread
    ]
    for ip, tier, score, z, ttl in mitigations:
        fields = {"tenant_id": primary_tenant, "ip": ip, "tier": tier, "score": score,
                  "reason": "behavioral_anomaly", "expires_at": epoch + ttl}
        if z is not None:
            fields["z"] = z
        MitigationStateTable(resource).put(**fields)

    # A row that has already lapsed. DynamoDB TTL deletes lazily — AWS says
    # up to 48 hours — so this is what the table really looks like, and the
    # portal must not list it as active.
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
    for tenant_id, share in [(primary_tenant, 0.55), ("globex-docs", 0.12),
                             ("initech-api", 0.26), ("hooli-legacy", 0.07)]:
        UsageCountersTable(resource).put(
            date=f"{today}{_TENANT_KEY_MARKER}{tenant_id}",
            total_requests=int(total * share),
        )

    out["tenants"] = [t[0] for t in tenants]
    return out
