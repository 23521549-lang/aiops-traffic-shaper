"""DynamoDB truncates every Query at 1MB, and nothing in this codebase noticed.

A grep for `LastEvaluatedKey` or `ExclusiveStartKey` across `services/backend`
returns zero hits. Every Query in the product issues one `query()` call and
returns `resp.get("Items", [])`.

For most tables that is harmless — they hold tens of items. For one it is
not, and the consequence is silent and ongoing:

`collect_training_vectors` calls `query_since(tenant_id, 0)` with no lower
bound (ml/feature_engineering.py), which queries the TenantIndex GSI for
every 5-second telemetry bucket a tenant has produced in the last 25 hours.
At five active IPs that is ~86,000 items of ~200 bytes ≈ 17MB — seventeen
pages. The nightly retrain has only ever seen the first one: **the ~5,000
OLDEST buckets in the window**, because a Query returns in sort-key order
ascending.

So the model is trained on a truncated, time-biased sample of its own
tenant's traffic, every night, and reports success. The tests did not catch
it because moto does not enforce the 1MB page limit, so in-process the
single page always contains everything.

This is not a chart feature. It is a correctness bug in the detection
pipeline that the chart work merely uncovered.
"""
import pytest

from services.backend.core.tables import TelemetryEventsTable, create_all_tables


class _PagingTable:
    """Stands in for a boto3 Table that returns results across pages.

    Needed because moto does not paginate: a test against moto proves only
    that the happy path works, which is exactly the reason this defect
    survived. This asserts on the contract DynamoDB actually has.
    """

    def __init__(self, pages):
        self._pages = pages
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        page = self._pages[len(self.calls) - 1]
        resp = {"Items": page["items"]}
        if "next" in page:
            resp["LastEvaluatedKey"] = page["next"]
        return resp


def _table(resource, pages):
    t = TelemetryEventsTable(resource)
    t._table = _PagingTable(pages)
    return t


def test_query_since_follows_every_page(dynamo_resource):
    create_all_tables(dynamo_resource)
    t = _table(dynamo_resource, [
        {"items": [{"bucket_start_ts": 1}, {"bucket_start_ts": 2}], "next": {"k": "a"}},
        {"items": [{"bucket_start_ts": 3}], "next": {"k": "b"}},
        {"items": [{"bucket_start_ts": 4}]},
    ])

    rows = t.query_since("t-1", 0)

    assert [r["bucket_start_ts"] for r in rows] == [1, 2, 3, 4]
    assert len(t._table.calls) == 3


def test_each_continuation_carries_the_previous_cursor(dynamo_resource):
    """Re-issuing the same query without ExclusiveStartKey is an infinite
    loop over page one — the failure mode a naive fix produces."""
    create_all_tables(dynamo_resource)
    t = _table(dynamo_resource, [
        {"items": [{"bucket_start_ts": 1}], "next": {"k": "a"}},
        {"items": [{"bucket_start_ts": 2}]},
    ])

    t.query_since("t-1", 0)

    assert "ExclusiveStartKey" not in t._table.calls[0]
    assert t._table.calls[1]["ExclusiveStartKey"] == {"k": "a"}


def test_a_single_page_result_makes_exactly_one_call(dynamo_resource):
    create_all_tables(dynamo_resource)
    t = _table(dynamo_resource, [{"items": [{"bucket_start_ts": 1}]}])

    assert len(t.query_since("t-1", 0)) == 1
    assert len(t._table.calls) == 1


def test_pagination_is_bounded(dynamo_resource):
    """An unbounded follow-the-cursor loop on a tenant with a very large
    window would run until the Lambda times out, which is a worse failure
    than truncation because it takes the whole request with it. The cap is
    generous enough for a legitimate 25-hour window and finite."""
    create_all_tables(dynamo_resource)
    pages = [{"items": [{"bucket_start_ts": i}], "next": {"k": str(i)}} for i in range(500)]
    t = _table(dynamo_resource, pages)

    rows = t.query_since("t-1", 0)

    assert len(t._table.calls) <= TelemetryEventsTable.MAX_QUERY_PAGES
    assert len(rows) == len(t._table.calls)


def test_the_other_paged_queries_follow_pages_too(dynamo_resource):
    """query_buckets_for_ip feeds flag_all_for_ip, the model-poisoning
    recovery lever. Truncating there means the lever silently only half
    works — it would flag the oldest buckets and leave the recent ones,
    which are the ones doing the damage."""
    create_all_tables(dynamo_resource)
    t = _table(dynamo_resource, [
        {"items": [{"bucket_start_ts": 1}], "next": {"k": "a"}},
        {"items": [{"bucket_start_ts": 2}]},
    ])

    rows = t.query_buckets_for_ip("t-1", "203.0.113.4")

    assert [r["bucket_start_ts"] for r in rows] == [1, 2]


def test_real_data_still_round_trips(dynamo_resource):
    """Against moto, i.e. the ordinary single-page case, unchanged."""
    create_all_tables(dynamo_resource)
    table = TelemetryEventsTable(dynamo_resource)
    for ts in (100, 200, 300):
        table.add_aggregate("t-1", "203.0.113.4", ts, {
            "request_count": 5, "error_count": 0, "post_count": 1,
            "total_bytes": 900, "total_time": 0.4,
            "distinct_uri_count": 2, "distinct_ua_count": 1,
        })

    assert len(table.query_since("t-1", 0)) == 3
    assert len(table.query_since("t-1", 250)) == 1


@pytest.mark.parametrize("method,args", [
    ("query_since", ("t-1", 0)),
    ("query_buckets_for_ip", ("t-1", "203.0.113.4")),
])
def test_an_empty_result_is_an_empty_list(dynamo_resource, method, args):
    create_all_tables(dynamo_resource)
    t = _table(dynamo_resource, [{"items": []}])
    assert getattr(t, method)(*args) == []
