"""A block the backend has stopped serving must come out of nginx.

`add_whitelist` deletes the MitigationState row, so the backend stops ISSUING
the block. The agent never noticed: `apply()` only adds and `sweep_expired()`
only removes on local TTL. The deny rule stood for up to an hour while the
console said "Any block on it has been lifted".

The set to reconcile against is the FULL active set, not the decisions in the
telemetry response. Those cover only IPs with traffic in that batch, and a
blocked IP stops sending traffic - that is what being blocked means. An
earlier draft of the spec got this wrong and would have unblocked every
attacker seconds after blocking them.
"""
import time

from services.agent.enforcer import DecisionStore
from services.agent.tests.test_runner import FakeAdapter, _collector, _record


def _blocked(ip, seconds=3600):
    return {"ip": ip, "tier": 2, "expires_at": int(time.time()) + seconds}


def test_an_ip_the_backend_no_longer_serves_is_unblocked():
    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7")], [adapter])

    lifted = store.reconcile(["198.51.100.1"], [adapter])

    assert lifted == ["203.0.113.7"]
    assert adapter.unblocked == ["203.0.113.7"]
    assert store.active_ips() == []


def test_an_ip_still_served_is_left_alone():
    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7")], [adapter])

    assert store.reconcile(["203.0.113.7"], [adapter]) == []
    assert adapter.unblocked == []


def test_an_empty_served_set_lifts_everything():
    """The backend accepted the batch and is issuing nothing. That is a real
    state - every timer expired - and it must reach the enforcers."""
    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7"), _blocked("198.51.100.1")], [adapter])

    assert sorted(store.reconcile([], [adapter])) == ["198.51.100.1", "203.0.113.7"]


def test_an_adapter_that_throws_does_not_strand_the_others():
    failing, ok = FakeAdapter(), FakeAdapter()
    failing.unblock = lambda ip: (_ for _ in ()).throw(OSError("iptables gone"))
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7")], [ok])

    store.reconcile([], [failing, ok])

    assert ok.unblocked == ["203.0.113.7"]


def test_a_failed_batch_never_lifts_anything():
    """The single most dangerous line in this feature. A backend outage must
    not read as "nothing is enforced any more" - that would strip every
    protection at exactly the moment an attack is causing the load."""
    from services.agent.http_client import BackendError
    from services.agent.runner import run_loop

    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7")], [adapter])

    collector = _collector([BackendError("503")], [], batch_size=2)
    run_loop([_record(), _record()], collector, store, [adapter])

    assert adapter.unblocked == []
    assert store.active_ips() == ["203.0.113.7"]


def test_an_accepted_batch_does_reconcile():
    from services.agent.runner import run_loop

    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7")], [adapter])

    collector = _collector([{"decisions": [], "active_ips": []}], [], batch_size=2)
    run_loop([_record(), _record()], collector, store, [adapter])

    assert adapter.unblocked == ["203.0.113.7"]


def test_a_backend_too_old_to_send_the_set_is_not_read_as_empty():
    """A response with no `active_ips` key at all means "this backend cannot
    tell me", which is different from "nothing is served". Reading the first
    as the second would release every block during a partial rollout."""
    from services.agent.runner import run_loop

    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7")], [adapter])

    collector = _collector([{"decisions": []}], [], batch_size=2)
    run_loop([_record(), _record()], collector, store, [adapter])

    assert adapter.unblocked == []
    assert store.active_ips() == ["203.0.113.7"]
