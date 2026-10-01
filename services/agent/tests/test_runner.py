"""The loop that connects the three pieces that were never connected.

`Collector` posts batches. `DecisionStore` applies and expires decisions.
`follow` reads the log. Until now nothing called all three, so the agent
could register and then do nothing at all, forever, while the console said
it had never reported.

What this loop must survive is the thing it exists for: the backend being
unreachable is one of the states a customer installs this product to find
out about. A loop that dies on a failed POST takes the enforcement with it,
including the sweep that removes blocks whose hour is up, so an outage on
our side would leave real visitors blocked on their server with nothing left
running to let them back in.
"""
import time

from services.agent.collector import Collector, LogRecord
from services.agent.enforcer import DecisionStore
from services.agent.enforcer.base import EnforcementAdapter
from services.agent.http_client import BackendError
from services.agent.runner import run_loop


class FakeAdapter(EnforcementAdapter):
    name = "fake"

    def __init__(self):
        self.blocked = []
        self.unblocked = []
        self.fail = False

    def is_available(self):
        return True

    def block(self, ip, tier, expires_at):
        if self.fail:
            raise OSError("iptables is not there")
        self.blocked.append((ip, tier, expires_at))
        return True

    def unblock(self, ip):
        self.unblocked.append(ip)
        return True


def _record(ip="203.0.113.7"):
    return LogRecord(time_iso8601="2026-09-23T10:00:00+07:00", remote_addr=ip,
                     request_method="GET", request_uri="/", status="200",
                     body_bytes_sent="10", request_time="0.01",
                     http_user_agent="curl/8.4.0")


def _collector(responses, sent, batch_size=2):
    def fake_post(url, payload, headers=None, **kw):
        sent.append(payload)
        result = responses.pop(0) if responses else {"decisions": []}
        if isinstance(result, Exception):
            raise result
        return result

    return Collector("https://backend", "t-1", "key", batch_size=batch_size,
                     post_json_fn=fake_post)


def test_records_reach_the_backend():
    sent = []
    run_loop([_record(), _record()], _collector([], sent), DecisionStore(), [])

    assert len(sent) == 1
    assert len(sent[0]["logs"]) == 2


def test_decisions_that_come_back_are_enforced():
    adapter = FakeAdapter()
    sent = []
    responses = [{"decisions": [{"ip": "203.0.113.7", "tier": 2,
                                 "expires_at": int(time.time()) + 3600}]}]

    run_loop([_record(), _record()], _collector(responses, sent),
             DecisionStore(), [adapter])

    assert adapter.blocked
    assert adapter.blocked[0][0] == "203.0.113.7"


def test_a_backend_failure_does_not_stop_the_agent():
    """The single most important line in this file. An outage on our side
    must not leave a customer with blocks in place and nothing running to
    remove them."""
    sent = []
    collector = _collector([BackendError("503"), {"decisions": []}], sent)

    run_loop([_record(), _record(), _record(), _record()], collector,
             DecisionStore(), [])

    assert len(sent) > 1  # it kept going and posted again


def test_a_batch_that_failed_to_send_is_retried_not_dropped():
    """`Collector.flush` clears its buffer only after a successful post, so
    the records from a failed batch go out with the next one. Written down
    because it is easy to "tidy" that into a clear-first flush and silently
    lose whatever was in flight during every blip."""
    sent = []
    collector = _collector([BackendError("503")], sent)

    run_loop([_record("198.51.100.1"), _record("198.51.100.2"),
              _record("198.51.100.3")], collector, DecisionStore(), [])

    delivered = [log["remote_addr"] for batch in sent for log in batch["logs"]]
    assert set(delivered) == {"198.51.100.1", "198.51.100.2", "198.51.100.3"}


def test_a_backend_that_stays_down_does_not_grow_the_buffer_forever():
    """The other half of retrying. A busy site during a long outage would
    buffer every request it serves, and the first batch to exceed the
    backend's own 1000-record cap is refused with a 422 - permanently, since
    the buffer only ever grows from there. The agent wedges itself shut on
    the day it is most needed, and it takes the machine's memory with it."""
    from services.agent.collector import MAX_BUFFERED_RECORDS

    sent = []
    collector = _collector([BackendError("503")] * 500, sent, batch_size=10)

    run_loop([_record() for _ in range(MAX_BUFFERED_RECORDS + 400)],
             collector, DecisionStore(), [])

    assert all(len(batch["logs"]) <= MAX_BUFFERED_RECORDS for batch in sent)


def test_expired_blocks_are_lifted_even_when_no_traffic_arrives():
    """Nothing in an nginx deny or an iptables DROP expires by itself, and a
    quiet site is exactly when a stale block does the most damage, because
    quiet is what a wrongly blocked customer looks like."""
    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([{"ip": "203.0.113.7", "tier": 2, "expires_at": int(time.time()) - 1}],
                [adapter])

    run_loop([None, None], _collector([], []), store, [adapter])

    assert adapter.unblocked == ["203.0.113.7"]


def test_an_adapter_that_throws_does_not_take_the_loop_with_it():
    adapter = FakeAdapter()
    adapter.fail = True
    sent = []
    responses = [{"decisions": [{"ip": "203.0.113.7", "tier": 2, "expires_at": 0}]},
                 {"decisions": []}]

    run_loop([_record(), _record(), _record(), _record()],
             _collector(responses, sent), DecisionStore(), [adapter])

    assert len(sent) == 2


def test_the_final_partial_batch_is_not_lost():
    """A stream that ends with fewer records than a full batch still has to
    send them. Otherwise the last thing that happened before a restart is
    the thing that never gets measured."""
    sent = []
    run_loop([_record()], _collector([], sent, batch_size=100), DecisionStore(), [])

    assert len(sent) == 1
    assert len(sent[0]["logs"]) == 1


def test_a_response_without_decisions_is_not_a_crash():
    """The backend answers with no decisions key when a tenant has no model
    yet, which is every tenant on their first day."""
    sent = []
    run_loop([_record(), _record()], _collector([{"received": 2}], sent),
             DecisionStore(), [])

    assert len(sent) == 1
