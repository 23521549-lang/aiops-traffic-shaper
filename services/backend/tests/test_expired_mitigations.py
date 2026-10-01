"""A card headed "Active mitigations" must not list expired ones.

`list_mitigations` returned every row `query_by_tenant` gave it, with no
regard for `expires_at`. That would be harmless if rows vanished the moment
they expired — but they do not. DynamoDB TTL is a background sweep, and AWS
documents deletion as typically within 48 hours of the expiry time, not at
it. So a tier-1 rate limit with a 300-second life stayed visible under the
word "Active" for up to two days after it stopped applying.

The two readers are affected differently and both matter:

  - The customer looks at the dashboard to decide whether to act. An
    expired row asks them to act on something that already resolved
    itself, and the "Expires" column shows a timestamp in the past, which
    reads as a bug in the product rather than as "this one is over".
  - The agent polls /agent/v1/decisions and writes what it gets into
    nginx. It sweeps expiries locally, so it recovers — but serving rows
    that are already dead makes the server's answer disagree with the
    server's own TTL, and any new agent that trusted the list would
    re-apply a block that ended days ago.

Filtering at the source fixes both and costs nothing: the rows are already
in memory from a query that had to happen anyway.
"""
import time

from services.backend.api.routes.agent import list_decisions
from services.backend.api.routes.dashboard import list_mitigations
from services.backend.core.tables import MitigationStateTable, create_all_tables


def _put(resource, ip, expires_at, tenant_id="t-1"):
    MitigationStateTable(resource).put(
        tenant_id=tenant_id, ip=ip, tier=1, score=-0.1, z=-4.2,
        reason="behavioral_anomaly", expires_at=expires_at,
    )


def test_an_expired_mitigation_is_not_listed_as_active(dynamo_resource):
    create_all_tables(dynamo_resource)
    now = int(time.time())
    _put(dynamo_resource, "203.0.113.1", now - 60)      # ended a minute ago
    _put(dynamo_resource, "203.0.113.2", now + 300)     # still in force

    shown = {m.ip for m in list_mitigations(tenant_id="t-1", resource=dynamo_resource)}
    assert shown == {"203.0.113.2"}


def test_the_agent_is_not_handed_decisions_that_already_ended(dynamo_resource):
    create_all_tables(dynamo_resource)
    now = int(time.time())
    _put(dynamo_resource, "203.0.113.1", now - 1)
    _put(dynamo_resource, "203.0.113.2", now + 3600)

    # Called as a plain function, so dependencies are passed by hand.
    # `list_decisions` takes the authenticated agent now: it has to honour
    # a tenant that has paused enforcement, and the tenant item is already
    # inside that dict, so reading it again would add a GetItem to the
    # agent's poll.
    agent = {"tenant_id": "t-1", "_tenant": {"tenant_id": "t-1"}}
    served = {d.ip for d in list_decisions(agent=agent, resource=dynamo_resource)}
    assert served == {"203.0.113.2"}


def test_a_mitigation_expiring_this_very_second_is_over(dynamo_resource):
    """`expires_at == now` means the TTL has elapsed. Off-by-one here would
    leave a row flickering between states on consecutive page loads."""
    create_all_tables(dynamo_resource)
    now = int(time.time())
    _put(dynamo_resource, "203.0.113.1", now)
    assert list_mitigations(tenant_id="t-1", resource=dynamo_resource) == []


def test_a_row_with_no_expiry_is_kept(dynamo_resource):
    """expires_at=0 was how the older tests wrote 'never mind the clock'.
    Treating a falsy expiry as 'expired long ago' would silently hide rows
    that some other code path might still write."""
    create_all_tables(dynamo_resource)
    _put(dynamo_resource, "203.0.113.1", 0)
    assert [m.ip for m in list_mitigations(tenant_id="t-1", resource=dynamo_resource)] \
        == ["203.0.113.1"]


def test_filtering_does_not_leak_across_tenants(dynamo_resource):
    create_all_tables(dynamo_resource)
    now = int(time.time())
    _put(dynamo_resource, "203.0.113.9", now + 300, tenant_id="t-2")
    assert list_mitigations(tenant_id="t-1", resource=dynamo_resource) == []
