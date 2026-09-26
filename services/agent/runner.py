"""The loop.

`Collector` posted batches, `DecisionStore` applied and expired decisions,
`follow` read the log, and nothing ever called all three. An agent could
register successfully and then do nothing at all, forever, while the console
reported that it had never sent anything.

The loop is written against an iterable of records rather than against
`follow` directly, so all of it is exercised by a list in a test. A run loop
that can only be driven by writing to a real file on a timer is a run loop
that ends up untested.
"""
import logging

from services.agent.http_client import BackendError

logger = logging.getLogger(__name__)


def run_loop(stream, collector, store, adapters) -> None:
    """Read, batch, post, enforce, expire.

    Every backend call is wrapped, because one of the states this product
    exists to report on is our own backend being unreachable. A loop that
    dies on a failed POST takes the expiry sweep down with it, and the
    customer is left with real visitors blocked on their server and nothing
    running that would ever let them back in. Surviving an outage is the
    behaviour, not a nicety.

    `stream` yields records, or None when there is nothing to read. That
    None is what drives the timed flush and the sweep on a site too quiet to
    fill a batch.
    """
    try:
        for record in stream:
            if record is not None:
                result = _call(collector.add, record)
            else:
                result = _call(collector.maybe_flush_on_interval)
            _apply(store, adapters, result)
            _sweep(store, adapters)
    finally:
        # Whatever is still buffered when the stream ends. Otherwise the
        # last thing that happened before a restart is the one thing never
        # measured.
        _apply(store, adapters, _call(collector.flush))
        _sweep(store, adapters)


def _call(fn, *args):
    """A backend call that cannot end the loop."""
    try:
        return fn(*args)
    except BackendError as e:
        logger.warning("backend unreachable (%s); the agent keeps running", e)
    except Exception:
        logger.exception("unexpected failure talking to the backend")
    return None


def _apply(store, adapters, result) -> None:
    """Enforce whatever came back, and stop enforcing whatever did not.

    A response with no decisions key at all is the normal answer for a
    tenant whose model has not been trained yet, which is every tenant on
    their first day.

    `result` is None when the POST failed, and that guard is load-bearing: a
    backend outage must never reconcile, because an empty served set would
    strip every block at exactly the moment an attack is causing the load.

    The `"active_ips" in result` check carries the same weight one step
    further in. A backend too old to send the key is saying "I cannot tell
    you", which is not the same as "nothing is served" — reading the first
    as the second would release every block during a partial rollout.
    """
    if not result:
        return
    try:
        # The customer has switched enforcement off in their console. The
        # release itself needs nothing new: the served set comes back empty
        # and the reconcile below drops every local rule, which is why the
        # switch works on agents that were installed before it existed. This
        # branch only makes the log say WHY, instead of leaving an operator
        # reading what looks like the backend having lost every decision.
        if result.get("enforce") is False and store.active_ips():
            logger.warning("enforcement paused for this tenant; releasing %d "
                           "local rule(s)", len(store.active_ips()))
        store.apply(result.get("decisions") or [], adapters)
        if "active_ips" in result:
            store.reconcile(result["active_ips"] or [], adapters)
    except Exception:
        logger.exception("applying decisions failed")


def _sweep(store, adapters) -> None:
    try:
        store.sweep_expired(adapters)
    except Exception:
        logger.exception("expiry sweep failed; blocks may outlive their timer")
