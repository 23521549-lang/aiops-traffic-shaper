# AI Traffic Shaper — Hybrid Free Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the backend as a single AWS Lambda (Always-Free forever)
serving a multi-tenant ML detection/mitigation API, and build the two new
components the old architecture never had — a thin agent the end user
installs, and a CLI to register it — so the product can be distributed for
free with the publisher's own infra cost target at 0đ.

**Architecture:** One FastAPI app behind a Lambda Function URL (Mangum
adapter), all state in DynamoDB (7 tables, see `docs/schema.md`), per-tenant
IsolationForest models stored as gzip-compressed DynamoDB binary items,
Cognito for dashboard/control-platform auth, EventBridge for the daily
retrain schedule. The agent is a separate, sklearn-free process that runs on
the tenant's own infrastructure and talks to the Lambda over HTTPS.

**Tech Stack:** Python 3.12, FastAPI + Mangum, boto3, DynamoDB, Cognito,
EventBridge, scikit-learn 1.5.0 (unchanged), Click (CLI). Local testing via
`moto` (DynamoDB mock) — no real AWS account needed until Stage 4's
`deploy-check` checkpoint.

**Spec:** `docs/PRD.md` (retro-PRD), `docs/adr/002-tech-stack-hybrid.md`
(stack decision, reuse/rewrite/discard table, measured model-size
constraint), `docs/schema.md` (DynamoDB tables), `docs/api-contract.md`
(endpoints).

## Global Constraints

- Infra cost target: **0đ, forever** — no AWS service outside the
  Always-Free-forever list (Lambda, DynamoDB, Cognito, EventBridge basic
  rules, SQS/SNS if needed later). No S3, no EFS, no API Gateway, no RDS,
  no NAT Gateway. (ADR-002)
- DynamoDB item hard limit: **400KB**. Model binary blobs MUST stay under
  this — verified `n_estimators=50` → ~238KB gzip; do not raise
  `n_estimators` without re-measuring (ADR-002, "Model-size finding").
- DynamoDB Always-Free capacity is **25 RCU + 25 WCU total, shared across
  every tenant** — this is the real ceiling for a DDoS-detection product
  (bursts are exactly when you need capacity most). Every write-heavy path
  (telemetry ingestion) must be designed as aggregate counters, not
  one-write-per-request-line (see Stage 2).
- No real AWS access in this environment. Every stage up through Stage 3
  must be fully testable locally via `moto`. Stage 4 onward needs a real
  AWS account for the final `deploy-check`; local dev still runs against
  `moto` first in every stage.
- Local Python env: WSL Ubuntu, venv at `~/aiops-venv` (already has
  scikit-learn 1.5.0/joblib/numpy matching `requirements.txt`, confirmed
  working in this session). Add `boto3`, `moto[dynamodb]`, `mangum`, `click`
  to a new `services/backend/requirements.txt` — do not touch the old
  `services/ai_engine/requirements.txt` / `services/worker_orchestrator/requirements.txt`,
  which stay as-is for reference (ADR-001, superseded).
- New code lives under `services/backend/` (Lambda) and `services/agent/`
  (thin client) — do not modify `services/ai_engine/` or
  `services/worker_orchestrator/` in place; copy/adapt the reusable pieces
  named in ADR-002's reuse table into the new package instead. This keeps
  the old, still-`git`-tracked implementation intact as a diffable
  reference while it's being ported.

---

## Stage 1 — DynamoDB data-access layer

**Goal:** A tested, moto-backed client module for the four "simple" tables
(`Tenants`, `Agents`, `Whitelist`, `MitigationState`) — the foundation every
later stage's storage calls go through. No feature/model logic yet.

**Files:**
- Create: `services/backend/requirements.txt`
- Create: `services/backend/core/dynamo.py`
- Create: `services/backend/core/tables.py`
- Test: `services/backend/tests/test_dynamo_tables.py`
- Test: `services/backend/tests/conftest.py` (moto fixture, shared by all later stages)

**Interfaces:**
- Produces: `get_dynamo_resource() -> boto3.resource("dynamodb")` in
  `core/dynamo.py` — every later module imports this, never instantiates
  its own boto3 client, so tests can swap it for `moto`.
- Produces: `create_all_tables(resource) -> None` in `core/tables.py` —
  idempotent, creates all 7 tables from `docs/schema.md` (only 4 populated
  with CRUD in this stage; the rest exist as empty tables so later stages
  don't need a migration step).
- Produces: `TenantsTable`, `AgentsTable`, `WhitelistTable`,
  `MitigationStateTable` classes in `core/tables.py`, each with `put`,
  `get`, `delete`, `query_by_tenant` methods matching the PK/SK from
  `docs/schema.md`.

- [ ] **Step 1: Write requirements.txt**

```
fastapi==0.134.0
mangum==0.17.0
boto3==1.34.144
pydantic==2.7.1
pydantic-settings==2.2.1
scikit-learn==1.5.0
pandas==2.2.2
numpy==1.26.4
click==8.1.7

# dev/test only
moto[dynamodb]==5.0.9
pytest==8.2.0
pytest-asyncio==0.23.6
```

- [ ] **Step 2: Write the failing test for table creation + Tenants CRUD**

```python
# services/backend/tests/conftest.py
import boto3
import pytest
from moto import mock_aws

@pytest.fixture
def dynamo_resource():
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="ap-southeast-1")
```

```python
# services/backend/tests/test_dynamo_tables.py
from services.backend.core.tables import create_all_tables, TenantsTable

def test_create_all_tables_is_idempotent(dynamo_resource):
    create_all_tables(dynamo_resource)
    create_all_tables(dynamo_resource)  # must not raise on second call
    existing = [t.name for t in dynamo_resource.tables.all()]
    assert "Tenants" in existing
    assert "Agents" in existing
    assert "Whitelist" in existing
    assert "MitigationState" in existing
    assert "Models" in existing
    assert "TelemetryEvents" in existing
    assert "UsageCounters" in existing

def test_tenants_put_and_get(dynamo_resource):
    create_all_tables(dynamo_resource)
    table = TenantsTable(dynamo_resource)
    table.put(tenant_id="t-1", name="Acme", contact_email="a@acme.test",
              created_at="2026-08-21T00:00:00Z", status="active")
    item = table.get(tenant_id="t-1")
    assert item["name"] == "Acme"
    assert item["status"] == "active"

def test_tenants_get_missing_returns_none(dynamo_resource):
    create_all_tables(dynamo_resource)
    table = TenantsTable(dynamo_resource)
    assert table.get(tenant_id="does-not-exist") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/backend && python -m pytest tests/test_dynamo_tables.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.backend.core.tables'`

- [ ] **Step 4: Implement `core/dynamo.py`**

```python
# services/backend/core/dynamo.py
import boto3

_REGION = "ap-southeast-1"

def get_dynamo_resource():
    return boto3.resource("dynamodb", region_name=_REGION)
```

- [ ] **Step 5: Implement `core/tables.py`**

```python
# services/backend/core/tables.py
from decimal import Decimal

from botocore.exceptions import ClientError


def _to_dynamo_safe(value):
    """boto3's DynamoDB resource API rejects native Python float ('Float
    types are not supported. Use Decimal types instead.') — found by
    actually running Stage 2's tests against moto, not something the plan
    anticipated on paper. Converts via str() to avoid binary-float
    artifacts. Applied recursively so every table that stores a float
    (this stage's total_time, Stage 3's model metadata, Stage 4's
    mitigation score, Stage 5's estimated_gb_seconds) is safe without
    repeating this at every call site."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamo_safe(v) for v in value]
    return value


# IMPORTANT: DynamoDB's Always-Free-forever allowance (25 RCU + 25 WCU,
# account-wide, across every table AND every GSI) applies ONLY to
# PROVISIONED billing mode. PAY_PER_REQUEST (on-demand) has NO free
# allowance and bills from the first request — using it here would
# silently break the project's core "0đ forever" constraint (ADR-002).
# Budget below sums to 15 WCU / 12 RCU, leaving headroom under 25/25.
_TABLE_SPECS = [
    {"TableName": "Tenants", "KeySchema": [{"AttributeName": "tenant_id", "KeyType": "HASH"}],
     "AttributeDefinitions": [{"AttributeName": "tenant_id", "AttributeType": "S"}],
     "ProvisionedThroughput": {"ReadCapacityUnits": 1, "WriteCapacityUnits": 1}},
    {"TableName": "Agents", "KeySchema": [
        {"AttributeName": "tenant_id", "KeyType": "HASH"},
        {"AttributeName": "agent_id", "KeyType": "RANGE"}],
     "AttributeDefinitions": [
        {"AttributeName": "tenant_id", "AttributeType": "S"},
        {"AttributeName": "agent_id", "AttributeType": "S"},
        {"AttributeName": "status", "AttributeType": "S"},
        {"AttributeName": "last_seen_at", "AttributeType": "S"}],
     "ProvisionedThroughput": {"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
     "GlobalSecondaryIndexes": [{
        "IndexName": "LastSeenIndex",
        "KeySchema": [
            {"AttributeName": "status", "KeyType": "HASH"},
            {"AttributeName": "last_seen_at", "KeyType": "RANGE"}],
        "Projection": {"ProjectionType": "ALL"},
        # GSI throughput is billed SEPARATELY from the base table and
        # counts against the same account-wide 25/25 free pool — budgeted
        # explicitly here, not an afterthought (Stage 6 needs this GSI).
        "ProvisionedThroughput": {"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
     }]},
    {"TableName": "Whitelist", "KeySchema": [
        {"AttributeName": "tenant_id", "KeyType": "HASH"},
        {"AttributeName": "ip", "KeyType": "RANGE"}],
     "AttributeDefinitions": [
        {"AttributeName": "tenant_id", "AttributeType": "S"},
        {"AttributeName": "ip", "AttributeType": "S"}],
     "ProvisionedThroughput": {"ReadCapacityUnits": 1, "WriteCapacityUnits": 1}},
    {"TableName": "MitigationState", "KeySchema": [
        {"AttributeName": "tenant_id", "KeyType": "HASH"},
        {"AttributeName": "ip", "KeyType": "RANGE"}],
     "AttributeDefinitions": [
        {"AttributeName": "tenant_id", "AttributeType": "S"},
        {"AttributeName": "ip", "AttributeType": "S"}],
     "ProvisionedThroughput": {"ReadCapacityUnits": 3, "WriteCapacityUnits": 3}},
    {"TableName": "Models", "KeySchema": [
        {"AttributeName": "tenant_id", "KeyType": "HASH"},
        {"AttributeName": "stage_version", "KeyType": "RANGE"}],
     "AttributeDefinitions": [
        {"AttributeName": "tenant_id", "AttributeType": "S"},
        {"AttributeName": "stage_version", "AttributeType": "S"}],
     # Low RCU is safe ONLY because Stage 4 caches the loaded model across
     # warm Lambda invocations — do not raise telemetry-path read volume
     # against this table without revisiting this number.
     "ProvisionedThroughput": {"ReadCapacityUnits": 2, "WriteCapacityUnits": 1}},
    {"TableName": "TelemetryEvents", "KeySchema": [
        {"AttributeName": "tenant_ip", "KeyType": "HASH"},
        {"AttributeName": "bucket_start_ts", "KeyType": "RANGE"}],
     "AttributeDefinitions": [
        {"AttributeName": "tenant_ip", "AttributeType": "S"},
        {"AttributeName": "bucket_start_ts", "AttributeType": "N"}],
     # Highest-write table by design (every unique IP per telemetry batch) —
     # gets the largest share of the WCU budget.
     "ProvisionedThroughput": {"ReadCapacityUnits": 2, "WriteCapacityUnits": 5}},
    {"TableName": "UsageCounters", "KeySchema": [{"AttributeName": "date", "KeyType": "HASH"}],
     "AttributeDefinitions": [{"AttributeName": "date", "AttributeType": "S"}],
     "ProvisionedThroughput": {"ReadCapacityUnits": 1, "WriteCapacityUnits": 2}},
]

def create_all_tables(resource) -> None:
    existing = {t.name for t in resource.tables.all()}
    for spec in _TABLE_SPECS:
        if spec["TableName"] in existing:
            continue
        try:
            resource.create_table(**spec)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceInUseException":
                raise


class _SimpleTable:
    _table_name: str
    _key_names: tuple[str, ...]

    def __init__(self, resource):
        self._resource = resource
        self._table = resource.Table(self._table_name)

    def put(self, **item) -> None:
        self._table.put_item(Item=_to_dynamo_safe(item))

    def get(self, **key) -> dict | None:
        resp = self._table.get_item(Key={k: key[k] for k in self._key_names})
        return resp.get("Item")

    def delete(self, **key) -> None:
        self._table.delete_item(Key={k: key[k] for k in self._key_names})

    def update(self, key: dict, update_expression: str,
               expr_names: dict | None = None, expr_values: dict | None = None) -> None:
        """Shared wrapper for update_item calls. Unlike put(), a bare
        update_item call does NOT go through _to_dynamo_safe — found on
        review after Stage 2 shipped: TelemetryEventsTable's add_aggregate
        had to convert its one float field by hand, which meant the next
        float field added via update_item elsewhere (e.g. Stage 5's
        UsageCounters) would silently hit the same float/Decimal error
        again. Routing every update_item call through this method makes
        the fix automatic everywhere, not just at the first call site."""
        kwargs = {"Key": key, "UpdateExpression": update_expression}
        if expr_names:
            kwargs["ExpressionAttributeNames"] = expr_names
        if expr_values:
            kwargs["ExpressionAttributeValues"] = _to_dynamo_safe(expr_values)
        self._table.update_item(**kwargs)

    def query_by_tenant(self, tenant_id: str) -> list[dict]:
        """Queries by this table's partition key — meaningful only for
        tables whose partition key IS a plain tenant_id. Uses
        self._key_names[0] rather than a hardcoded "tenant_id" string —
        found on review: the hardcoded version would raise a DynamoDB
        ValidationException (or silently query the wrong attribute) on any
        table whose partition key is named differently. See
        TelemetryEventsTable's override below, which rejects this call
        outright instead of returning a misleading result."""
        from boto3.dynamodb.conditions import Key
        partition_key = self._key_names[0]
        resp = self._table.query(KeyConditionExpression=Key(partition_key).eq(tenant_id))
        return resp.get("Items", [])


class TenantsTable(_SimpleTable):
    _table_name = "Tenants"
    _key_names = ("tenant_id",)

class AgentsTable(_SimpleTable):
    _table_name = "Agents"
    _key_names = ("tenant_id", "agent_id")

class WhitelistTable(_SimpleTable):
    _table_name = "Whitelist"
    _key_names = ("tenant_id", "ip")

class MitigationStateTable(_SimpleTable):
    _table_name = "MitigationState"
    _key_names = ("tenant_id", "ip")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd services/backend && python -m pytest tests/test_dynamo_tables.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add services/backend/requirements.txt services/backend/core/dynamo.py \
        services/backend/core/tables.py services/backend/tests/conftest.py \
        services/backend/tests/test_dynamo_tables.py
git commit -m "feat(backend): add moto-tested DynamoDB data-access layer"
```

**Checkpoint (stage done when):**
`cd services/backend && python -m pytest tests/test_dynamo_tables.py -v`
→ all pass, zero real AWS calls (moto only).

---

## Stage 2 — Telemetry aggregation & feature computation on DynamoDB (highest technical risk — do this before anything depends on it)

**Goal:** Replace the old Redis `ZADD`/`ZRANGEBYSCORE` sliding window
(`ai_engine/ml/feature_engineering.py`, `window_seconds=5`) with a
DynamoDB-native design that fits inside the 25 WCU/RCU Always-Free ceiling.

**Design decisions, made here because they follow directly from the
already-agreed 0đ constraint:**

1. **Aggregate in Lambda memory before touching DynamoDB.** A batch can
   contain many log lines for the same IP (that's exactly what a DDoS
   burst looks like). Grouping and summing in Python first, then issuing
   **exactly one `UpdateItem` per unique IP per batch**, is what actually
   keeps writes at 1/IP/batch — looping the DynamoDB call per log line
   (a mistake caught and fixed in this same pass) would silently recreate
   the old Redis design's per-line write cost.
2. **Bounded numeric aggregates only — no growing lists.** The first draft
   stored raw `uris`/`user_agents` via `list_append`, which grows the item
   without bound and gets most expensive exactly during a real attack
   (more requests → bigger item → more WCU per write). Replaced with
   fixed-size numeric fields (`distinct_uri_count`, `distinct_ua_count`)
   computed once per batch in Python and added via numeric `ADD` — O(1)
   item size regardless of traffic volume. Trade-off, stated explicitly:
   `user_agent_entropy` becomes an approximation (`distinct_ua_count / total`,
   a diversity ratio) rather than true Shannon entropy merged across
   batches — true entropy isn't additive across separately-computed
   batches, and storing a full frequency distribution reintroduces the
   unbounded-growth problem this fix exists to remove.
3. **Sliding-window counter (2 buckets, weighted) instead of one fixed
   bucket.** A single fixed 5-second bucket lets an attacker split volume
   across a bucket boundary (half the requests in bucket N, half in N+1,
   each individually under threshold). Reading the *current* bucket plus a
   *weighted* fraction of the *previous* bucket — the standard "sliding
   window counter" rate-limiting algorithm — closes this gap at the cost
   of one extra `GetItem` per feature computation (still O(1), not a scan).

**Files:**
- Create: `services/backend/ml/feature_engineering.py`
- Create: `services/backend/core/tables.py` — extend with `TelemetryEventsTable`
  (append to the file from Stage 1, not a new file)
- Test: `services/backend/tests/test_feature_engineering.py`

**Interfaces:**
- Consumes: `TenantsTable`/etc. patterns from Stage 1 (`_SimpleTable`),
  `get_dynamo_resource()`.
- Produces: `FeatureVector` dataclass (same 7 fields + `sample_size` as the
  old `ai_engine/ml/feature_engineering.py::FeatureVector` — kept
  byte-for-byte identical so `services/backend/ml/model.py` in Stage 3 can
  reuse `to_list()`/`to_dict()` unmodified).
- Produces: `record_batch(resource, tenant_id: str, logs: list[LogRecord], bucket_seconds: int = 5) -> set[str]`
  — the new equivalent of the old `store_logs_to_window`; aggregates the
  whole batch in memory FIRST, then issues exactly one `UpdateItem` per
  unique IP. Returns the unique IPs touched in this batch.
- Produces: `compute_features_for_ip(resource, tenant_id: str, ip: str, bucket_seconds: int = 5) -> FeatureVector | None`
  — reads current bucket + weighted previous bucket (sliding-window
  counter), not a single fixed bucket.

- [ ] **Step 1: Write the failing test for bucketed aggregate writes**

```python
# services/backend/tests/test_feature_engineering.py
import time
from services.backend.core.tables import create_all_tables
from services.backend.ml.feature_engineering import record_batch, compute_features_for_ip

class _Log:
    def __init__(self, remote_addr, status="200", body_bytes_sent="512",
                 request_time="0.05", request_uri="/a", request_method="GET",
                 http_user_agent="ua-1"):
        self.remote_addr = remote_addr
        self.status = status
        self.body_bytes_sent = body_bytes_sent
        self.request_time = request_time
        self.request_uri = request_uri
        self.request_method = request_method
        self.http_user_agent = http_user_agent

def test_record_batch_issues_one_write_per_unique_ip_not_per_log_line(dynamo_resource, monkeypatch):
    create_all_tables(dynamo_resource)
    from services.backend.core import tables as tables_mod
    call_count = {"n": 0}
    original = tables_mod.TelemetryEventsTable.add_aggregate
    def _counting_add_aggregate(self, *a, **kw):
        call_count["n"] += 1
        return original(self, *a, **kw)
    monkeypatch.setattr(tables_mod.TelemetryEventsTable, "add_aggregate", _counting_add_aggregate)

    logs = [_Log("1.2.3.4") for _ in range(5)] + [_Log("9.9.9.9")]  # 5 lines, 1 IP + 1 line, 1 IP
    touched = record_batch(dynamo_resource, "t-1", logs, bucket_seconds=5)
    assert touched == {"1.2.3.4", "9.9.9.9"}
    # exactly 2 DynamoDB writes for 6 log lines across 2 IPs — NOT 6
    assert call_count["n"] == 2

def test_compute_features_below_threshold_returns_none(dynamo_resource):
    create_all_tables(dynamo_resource)
    record_batch(dynamo_resource, "t-1", [_Log("1.2.3.4")], bucket_seconds=5)
    # min_requests_threshold=3 (same default as the old ai_engine settings)
    vector = compute_features_for_ip(dynamo_resource, "t-1", "1.2.3.4",
                                      bucket_seconds=5, min_requests_threshold=3)
    assert vector is None

def test_compute_features_above_threshold(dynamo_resource):
    create_all_tables(dynamo_resource)
    logs = [_Log("1.2.3.4", status="500")] * 2 + [_Log("1.2.3.4", status="200")]
    record_batch(dynamo_resource, "t-1", logs, bucket_seconds=5)
    vector = compute_features_for_ip(dynamo_resource, "t-1", "1.2.3.4",
                                      bucket_seconds=5, min_requests_threshold=3)
    assert vector is not None
    assert vector.sample_size == 3
    assert round(vector.error_ratio, 3) == round(2 / 3, 3)

def test_split_burst_across_bucket_boundary_still_detected(dynamo_resource):
    # The exact evasion the old fixed-bucket-only design was vulnerable to:
    # attacker sends half a burst right before a bucket boundary, half
    # right after. Each half alone might look clean; the sliding-window
    # counter must still see the combined rate.
    create_all_tables(dynamo_resource)
    prev_bucket = 1000  # bucket_seconds=5 -> boundary at t=1000
    logs_prev = [_Log("1.2.3.4") for _ in range(20)]
    record_batch(dynamo_resource, "t-1", logs_prev, bucket_seconds=5, now=1004.9)
    logs_curr = [_Log("1.2.3.4") for _ in range(20)]
    record_batch(dynamo_resource, "t-1", logs_curr, bucket_seconds=5, now=1005.1)

    vector = compute_features_for_ip(dynamo_resource, "t-1", "1.2.3.4",
                                      bucket_seconds=5, min_requests_threshold=3,
                                      now=1005.1)
    assert vector is not None
    # near the boundary, the weighted sliding count should reflect close to
    # the full 40 requests, not just the 20 in the current bucket alone
    assert vector.sample_size > 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/backend && python -m pytest tests/test_feature_engineering.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Extend `core/tables.py` with `TelemetryEventsTable`**

```python
# append to services/backend/core/tables.py
class TelemetryEventsTable(_SimpleTable):
    _table_name = "TelemetryEvents"
    _key_names = ("tenant_ip", "bucket_start_ts")

    def add_aggregate(self, tenant_ip: str, bucket_start_ts: int, agg: dict,
                       ttl_seconds: int = 3600) -> None:
        """Single atomic write of an ALREADY-AGGREGATED batch summary for
        one (tenant, ip, bucket) — never called per log line. `agg` keys:
        request_count, error_count, post_count, total_bytes, total_time,
        distinct_uri_count, distinct_ua_count — all plain numbers, so the
        item stays a fixed handful of bytes regardless of traffic volume
        (no lists, nothing that grows with request count)."""
        self.update(
            key={"tenant_ip": tenant_ip, "bucket_start_ts": bucket_start_ts},
            update_expression=(
                "ADD request_count :rc, error_count :ec, post_count :pc, "
                "total_bytes :tb, total_time :tt, "
                "distinct_uri_count :du, distinct_ua_count :da "
                "SET #ttl = :ttl"
            ),
            expr_names={"#ttl": "ttl"},
            expr_values={
                ":rc": agg["request_count"], ":ec": agg["error_count"],
                ":pc": agg["post_count"], ":tb": agg["total_bytes"],
                ":tt": agg["total_time"], ":du": agg["distinct_uri_count"],
                ":da": agg["distinct_ua_count"],
                ":ttl": bucket_start_ts + ttl_seconds,
            },
        )

    def get_bucket(self, tenant_ip: str, bucket_start_ts: int) -> dict | None:
        return self.get(tenant_ip=tenant_ip, bucket_start_ts=bucket_start_ts)

    def get_buckets_batch(self, tenant_ip: str, bucket_starts: list[int]) -> dict[int, dict]:
        """Fetch multiple buckets for the same tenant_ip in one DynamoDB
        BatchGetItem call instead of one GetItem per bucket — found on
        review: the sliding-window read below issued two independent
        sequential GetItem calls (current + previous bucket) that don't
        depend on each other's result. Same total RCU cost, fewer round
        trips on the hot telemetry-scoring path. Retries any
        UnprocessedKeys up to 3 times before giving up on the remainder."""
        if not bucket_starts:
            return {}
        pending = [{"tenant_ip": tenant_ip, "bucket_start_ts": ts} for ts in bucket_starts]
        found: dict[int, dict] = {}
        for _ in range(3):
            if not pending:
                break
            resp = self._resource.batch_get_item(RequestItems={self._table_name: {"Keys": pending}})
            for item in resp.get("Responses", {}).get(self._table_name, []):
                found[int(item["bucket_start_ts"])] = item
            pending = resp.get("UnprocessedKeys", {}).get(self._table_name, {}).get("Keys", [])
        return found

    def query_by_tenant(self, tenant_id: str) -> list[dict]:
        # Partition key here is "{tenant_id}#{ip}", a composite — NOT a
        # plain tenant_id — so it can't be queried by tenant alone
        # (DynamoDB Query only supports begins_with on a SORT key, not the
        # partition key). Found on review: the inherited base
        # implementation would have silently queried the wrong attribute.
        # A caller needing "all telemetry for tenant X" needs a GSI on
        # tenant_id, which doesn't exist yet — fail loudly instead.
        raise NotImplementedError(
            "TelemetryEventsTable's partition key is a tenant_id#ip composite, "
            "not a plain tenant_id — query_by_tenant() doesn't apply here. "
            "Use get_bucket()/get_buckets_batch() for specific IP/bucket lookups."
        )
```

- [ ] **Step 4: Implement `ml/feature_engineering.py`**

```python
# services/backend/ml/feature_engineering.py
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
    # any single IP contributed (a batch-per-line loop here would silently
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
    # neither bucket depends on the other's result (found on review).
    buckets  = table.get_buckets_batch(tenant_ip, [current_start, previous_start])
    current  = buckets.get(current_start, {})
    previous = buckets.get(previous_start, {})
    prev_weight = 1.0 - elapsed_fraction

    def _w(field: str, cast=int) -> float:
        return cast(current.get(field, 0)) + prev_weight * cast(previous.get(field, 0))

    total = _w("request_count")
    if total < min_requests_threshold:
        return None

    distinct_uri = _w("distinct_uri_count")
    distinct_ua  = _w("distinct_ua_count")

    return FeatureVector(
        remote_addr=ip,
        request_rate=round(total / bucket_seconds, 6),
        error_ratio=round(_w("error_count") / total, 6),
        avg_bytes_sent=round(_w("total_bytes") / total, 6),
        avg_request_time=round(_w("total_time", float) / total, 6),
        unique_uri_ratio=round(min(distinct_uri / total, 1.0), 6),
        # Approximation, not true Shannon entropy — see Stage 2 design
        # note: true entropy doesn't merge additively across buckets, and
        # storing a full frequency distribution reintroduces unbounded
        # item growth. distinct_ua/total is a bounded diversity proxy.
        user_agent_entropy=round(min(distinct_ua / total, 1.0), 6),
        post_ratio=round(_w("post_count") / total, 6),
        sample_size=int(total),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/backend && python -m pytest tests/test_feature_engineering.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add services/backend/ml/feature_engineering.py services/backend/core/tables.py \
        services/backend/tests/test_feature_engineering.py
git commit -m "feat(backend): DynamoDB-native sliding-window feature aggregation (replaces Redis sliding window)"
```

**Checkpoint (stage done when):**
`cd services/backend && python -m pytest tests/test_feature_engineering.py -v`
→ all 4 pass, including `test_record_batch_issues_one_write_per_unique_ip_not_per_log_line`
(proves the WCU story: 1 write per unique IP per batch, not per log line —
this is the number that must stay far below the 5 WCU provisioned on
`TelemetryEvents` in Stage 1 at expected load) and
`test_split_burst_across_bucket_boundary_still_detected` (proves the
sliding-window-counter closes the fixed-bucket evasion gap). Re-check the
WCU number against real traffic estimates before Stage 8 (agent) goes live
for real users.

**Found while actually running this stage (2026-08-21):** boto3's
DynamoDB resource API raises `TypeError: Float types are not supported.
Use Decimal types instead.` on any native Python float — code in this plan
had never been executed before this pass. Fixed with a recursive
`_to_dynamo_safe()` helper in `core/tables.py`, applied in `_SimpleTable.put()`
and `TelemetryEventsTable.add_aggregate()`. Every later stage that stores a
float (Stage 3 model metadata, Stage 4 mitigation score, Stage 5 usage
GB-seconds) inherits this fix automatically through `put()` — do not
special-case it again per table.

---

## Stage 3 — Model registry rewrite + ML core port

**Goal:** Port `ai_engine/ml/model.py`, `training.py`, `validator.py`,
`monitoring.py` into `services/backend/ml/`, adding `tenant_id` scoping,
and replace `ai_engine/ml/registry.py`'s local-filesystem storage
(`/app/models`, incompatible with stateless Lambda) with the DynamoDB
binary-item storage decided in ADR-002 (`n_estimators=50`, gzip, single
item, per tenant).

**Files:**
- Create: `services/backend/ml/registry.py`
- Create: `services/backend/ml/model.py` (ported from `ai_engine/ml/model.py`, add `tenant_id` param)
- Create: `services/backend/ml/training.py` (ported from `ai_engine/ml/training.py`, `n_estimators=50`)
- Create: `services/backend/core/tables.py` — extend with `ModelsTable`
- Test: `services/backend/tests/test_registry.py`

**Interfaces:**
- Consumes: `FeatureVector` from Stage 2's `ml/feature_engineering.py`.
- Produces: `save_model(resource, tenant_id: str, model: IsolationForest, metadata: ModelMetadata, stage: str = "staging") -> None`
  — raises `ValueError("model exceeds DynamoDB item limit")` if the
  gzip-compressed blob is over 400,000 bytes, so an accidental
  `n_estimators` bump fails loudly in CI instead of failing silently in
  production.
- Produces: `load_model(resource, tenant_id: str, stage: str = "production") -> IsolationForest | None`.
- Produces: `ModelManager.score_vectors(vectors: list[FeatureVector]) -> list[tuple[FeatureVector, float]]`
  — same signature as the old `ai_engine/ml/model.py::ModelManager`, so
  Stage 4's telemetry handler calls it identically. `ModelManager.load()`
  caches per-tenant models at class level across warm Lambda invocations
  (see Step 6) — this is load-bearing for staying inside the RCU budget,
  not an optional optimization.

- [ ] **Step 1: Extend `core/tables.py` with `ModelsTable` (binary put/get + size guard)**

```python
# append to services/backend/core/tables.py
class ModelsTable(_SimpleTable):
    _table_name = "Models"
    _key_names = ("tenant_id", "stage_version")

    MAX_BLOB_BYTES = 400_000

    def put_model_blob(self, tenant_id: str, stage_version: str, blob: bytes, **metadata) -> None:
        if len(blob) > self.MAX_BLOB_BYTES:
            raise ValueError(
                f"model blob {len(blob)} bytes exceeds DynamoDB item limit "
                f"{self.MAX_BLOB_BYTES} — reduce n_estimators (see ADR-002)"
            )
        self.put(tenant_id=tenant_id, stage_version=stage_version, model_blob=blob, **metadata)
```

- [ ] **Step 2: Write the failing test**

```python
# services/backend/tests/test_registry.py
import numpy as np
import pytest
from sklearn.ensemble import IsolationForest
from services.backend.core.tables import create_all_tables
from services.backend.ml.registry import save_model, load_model, ModelMetadata

def _trained_model(n_estimators=50):
    X = np.random.rand(500, 7)
    return IsolationForest(n_estimators=n_estimators, contamination=0.05, random_state=0).fit(X)

def test_save_and_load_model_roundtrip(dynamo_resource):
    create_all_tables(dynamo_resource)
    model = _trained_model()
    meta = ModelMetadata(version="v1", trained_at="2026-08-21T00:00:00Z",
                          training_samples=500, contamination=0.05,
                          score_mean=0.0, score_std=1.0,
                          features=["request_rate"], stage="production")
    save_model(dynamo_resource, "t-1", model, meta, stage="production")
    loaded = load_model(dynamo_resource, "t-1", stage="production")
    assert loaded is not None
    assert loaded.n_estimators == 50

def test_save_model_rejects_oversized_blob(dynamo_resource):
    create_all_tables(dynamo_resource)
    model = _trained_model(n_estimators=100)  # measured ~474KB gzip in ADR-002 — over limit
    meta = ModelMetadata(version="v1", trained_at="2026-08-21T00:00:00Z",
                          training_samples=500, contamination=0.05,
                          score_mean=0.0, score_std=1.0,
                          features=["request_rate"], stage="production")
    with pytest.raises(ValueError, match="exceeds DynamoDB item limit"):
        save_model(dynamo_resource, "t-1", model, meta, stage="production")

def test_load_missing_model_returns_none(dynamo_resource):
    create_all_tables(dynamo_resource)
    assert load_model(dynamo_resource, "no-such-tenant") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/backend && python -m pytest tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement `ml/registry.py`**

```python
# services/backend/ml/registry.py
import gzip
import io
import logging
from dataclasses import asdict, dataclass

import joblib
from sklearn.ensemble import IsolationForest

from services.backend.core.tables import ModelsTable

logger = logging.getLogger(__name__)


@dataclass
class ModelMetadata:
    version: str
    trained_at: str
    training_samples: int
    contamination: float
    score_mean: float
    score_std: float
    features: list[str]
    stage: str

    def to_dict(self) -> dict:
        return asdict(self)


def save_model(resource, tenant_id: str, model: IsolationForest,
                metadata: ModelMetadata, stage: str = "staging") -> None:
    buf = io.BytesIO()
    joblib.dump(model, buf)
    blob = gzip.compress(buf.getvalue())

    table = ModelsTable(resource)
    table.put_model_blob(tenant_id, stage, blob, **metadata.to_dict())


def load_model(resource, tenant_id: str, stage: str = "production") -> IsolationForest | None:
    table = ModelsTable(resource)
    item = table.get(tenant_id=tenant_id, stage_version=stage)
    if item is None:
        return None
    try:
        raw = gzip.decompress(bytes(item["model_blob"]))
        return joblib.load(io.BytesIO(raw))
    except Exception as e:
        # A corrupted blob or a joblib/sklearn version mismatch between the
        # training Lambda and the serving Lambda must fall back to shadow
        # mode (None), not crash the telemetry request with an unhandled
        # 500 — found on review: this try/except was missing from the
        # first draft, unlike the old ai_engine/ml/registry.py it was
        # ported from.
        logger.error("Failed to deserialize model: tenant=%s stage=%s: %s",
                     tenant_id, stage, e)
        return None


def model_exists(resource, tenant_id: str, stage: str = "production") -> bool:
    table = ModelsTable(resource)
    return table.get(tenant_id=tenant_id, stage_version=stage) is not None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/backend && python -m pytest tests/test_registry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Port `ml/model.py` and `ml/training.py`, adding a module-level cache to `ModelManager`**

**Why this step exists (found and fixed in this pass):** a naive port that
calls `registry.load_model` on every `ModelManager.load()` would read the
full ~238KB compressed model item on **every single telemetry request** —
a strongly-consistent read of an item that size costs roughly 60 RCU,
which alone blows past the entire 25 RCU/sec account-wide budget in one
request. Lambda reuses its execution environment across "warm" invocations
of the same container — module-level state survives between calls — so
the fix is to cache the deserialized model there and only hit DynamoDB
again on a cold start.

Copy `services/ai_engine/ml/model.py` → `services/backend/ml/model.py` and
`services/ai_engine/ml/training.py` → `services/backend/ml/training.py`.
Required edits (do NOT change anything else — this is a port, not a
rewrite):
- `ModelManager` gets a class-level `_cache: dict[str, IsolationForest] = {}`
  shared by every instance in the container. `load(resource, tenant_id)`
  checks `_cache` first; only calls `registry.load_model` on a cache miss.
  **Import `registry` as a module (`from services.backend.ml import
  registry`) and call `registry.load_model(...)`, not `from ...registry
  import load_model`** — found while writing this stage's cache test: a
  `from module import name` binds a local reference at import time, so
  `monkeypatch.setattr(registry, "load_model", ...)` in a test silently
  doesn't affect the already-bound name inside `model.py`. Qualified
  access is also just better practice here regardless of testing.
- `score_vectors()` is synchronous, not `async def` — the old `ai_engine`
  version used `anyio.to_thread.run_sync` because it ran inside a
  long-lived async FastAPI app. A single Mangum-wrapped Lambda invocation
  has no such event loop to protect; Starlette runs sync routes in a
  threadpool automatically, so the async wrapper added nothing here and
  was dropped during the port.
  **Explicit trade-off, not an oversight:** this means a warm container can
  keep serving a stale model for up to that container's lifetime after a
  retrain promotes a new version (Stage 7) — no version-check read is
  added, because a cheap version-check would itself need a DynamoDB read
  on every request, reintroducing the exact cost problem this fix removes.
  Lambda containers recycle naturally (AWS-managed, not app-managed); if
  faster propagation is ever needed, add a manual "flush cache" control-
  platform action rather than a per-request check.
- `reload()` clears this tenant's cache entry and calls `load()` again —
  used by the manual-retrain path only, not the hot path.
- `training.py` is NOT a line-for-line port — the old file's shadow-data
  collection (Redis `XADD`/`XRANGE` stream) and `AsyncIOScheduler` wiring
  don't exist in Lambda (no long-lived process to run a scheduler in).
  Stage 3 ships a narrower `train_and_save(resource, tenant_id,
  feature_vectors: list[list[float]], stage="staging", contamination=0.01)
  -> ModelMetadata | None` — given already-collected vectors, train
  `IsolationForest(n_estimators=50, ...)` (ADR-002 measured constraint)
  and save via `registry.save_model`. Collecting those vectors from
  `TelemetryEvents` and looping every tenant on a schedule is Stage 7's
  job (EventBridge entry point), not duplicated here.
- Remove the old `feature_config.py` import (`get_enabled_feature_names`,
  `get_feature_count`) — Stage 2's `feature_engineering.py` already has
  `FEATURE_NAMES` as a fixed module-level constant; use that directly
  instead of the old configurable-registry pattern (the registry pattern
  added no value here and is one less file to port).
- `score_vectors()` gets a single `except Exception` block, not a separate
  `except ValueError` above it — found on review: the two branches had
  identical bodies (`ValueError` is already an `Exception` subclass), so
  the split added nothing but noise.
- `services/backend/requirements.txt` does not include `pandas` — found on
  review: nothing in `services/backend` imports it (unlike the old
  `ai_engine`), and this project is otherwise deliberately careful about
  Lambda package size (see the 400KB model-blob limit).

- [ ] **Step 7: Write tests for cache behavior and classify_score thresholds**

```python
# services/backend/tests/test_model_port.py
import numpy as np
from sklearn.ensemble import IsolationForest as SKIsolationForest
from services.backend.core.tables import create_all_tables
from services.backend.ml import registry
from services.backend.ml.model import classify_score, AnomalyTier, ModelManager

def test_classify_score_thresholds_match_old_service():
    # TIER1_THRESHOLD = -0.1, TIER2_THRESHOLD = -0.3 — unchanged from
    # ai_engine/ml/model.py, must not silently drift during the port
    assert classify_score(0.5) == AnomalyTier.NORMAL
    assert classify_score(-0.15) == AnomalyTier.RATE_LIMIT
    assert classify_score(-0.35) == AnomalyTier.HARD_BLOCK

def test_model_manager_only_reads_dynamodb_once_across_warm_calls(dynamo_resource, monkeypatch):
    create_all_tables(dynamo_resource)
    X = np.random.rand(200, 7)
    model = SKIsolationForest(n_estimators=50, random_state=0).fit(X)
    registry.save_model(dynamo_resource, "t-1", model, registry.ModelMetadata(
        version="v1", trained_at="2026-08-21T00:00:00Z", training_samples=200,
        contamination=0.05, score_mean=0.0, score_std=1.0,
        features=["request_rate"], stage="production"), stage="production")
    # NOTE: `stage=` kwarg to save_model controls the actual DynamoDB
    # stage_version key — it does NOT read metadata.stage. Passing only
    # metadata.stage="production" without also passing stage="production"
    # here silently saves under "staging" instead (found while writing
    # this test).

    ModelManager._cache.clear()  # simulate a fresh cold start
    read_count = {"n": 0}
    original = registry.load_model
    def _counting_load(*a, **kw):
        read_count["n"] += 1
        return original(*a, **kw)
    monkeypatch.setattr(registry, "load_model", _counting_load)
    # NOTE: this monkeypatch only works because model.py calls
    # registry.load_model(...) (qualified), not a bare load_model() bound
    # via `from ...registry import load_model` at import time — see the
    # note on Step 6 above.

    mgr1 = ModelManager()
    mgr1.load(dynamo_resource, "t-1")
    mgr2 = ModelManager()  # simulates the next warm invocation, new instance
    mgr2.load(dynamo_resource, "t-1")

    assert read_count["n"] == 1  # second `load()` hit the cache, not DynamoDB
```

Run: `cd services/backend && python -m pytest tests/test_model_port.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add services/backend/ml/
git commit -m "feat(backend): port ML core to backend package, DynamoDB model storage"
```

**Checkpoint (stage done when):**
`cd services/backend && python -m pytest tests/ -v` → all Stage 1-3 tests
pass (moto only, no real AWS).

---

## Stage 4 — Lambda API handler + agent-facing endpoints

**Goal:** Wire Stages 1-3 into the FastAPI app + Mangum handler, implement
`/agent/v1/register`, `/agent/v1/telemetry`, `/agent/v1/decisions` from
`docs/api-contract.md`.

**Files:**
- Create: `services/backend/main.py` (FastAPI app + `handler = Mangum(app)`)
- Create: `services/backend/api/routes/agent.py`
- Create: `services/backend/schemas/telemetry.py` (port `LogRecord`,
  `TelemetryBatch` from `services/ai_engine/schemas/telemetry.py` unchanged
  — pure Pydantic, no infra coupling, nothing to adapt)
- Create: `services/backend/api/dependencies.py` (agent API-key auth,
  hash-compares against `AgentsTable`)
- Test: `services/backend/tests/test_agent_routes.py` (FastAPI `TestClient`, moto DynamoDB)

**Interfaces:**
- Consumes: `ModelManager`, `classify_score`, `AnomalyTier` (Stage 3),
  `record_batch`/`compute_features_for_ip` (Stage 2), `AgentsTable`,
  `MitigationStateTable`, `WhitelistTable` (Stage 1).
- Produces: `POST /agent/v1/telemetry` returning `TelemetryResponse` with a
  `decisions: list[MitigationState]` field per `docs/api-contract.md` —
  this is what Stage 8's agent enforces locally. Also `GET
  /agent/v1/decisions` (agent-key-authenticated, so it belongs in this
  stage — `/agent/v1/register` needs Cognito and waits for Stage 6).

**Design correction made before writing any code (caught reviewing the
plan itself, not by running it):** the version of this stage originally
drafted here used a hand-rolled `dynamo_resource_override()` that
reassigns a module-level `_resource` global, with `build_agent_router(_resource, ...)`
called once at import time to build the router. That does **not work** —
`build_agent_router`'s `resource` parameter closes over whatever object
`_resource` pointed to at that one call, and reassigning the module global
afterward has zero effect on the already-built router. A test relying on
this would either hit real AWS (unreachable in this environment) or
silently pass against the wrong resource. Fixed with FastAPI's own
dependency-injection: a `get_dynamo_resource` dependency resolved fresh
per request via `Depends(...)`, swappable correctly in tests via
`app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource`.
`agent_auth` becomes a plain dependency function (not a factory) for the
same reason. The code below reflects the corrected design — no separate
`build_agent_router()`/`dynamo_resource_override()` functions exist.

- [ ] **Step 1: Implement `api/dependencies.py`**

```python
# services/backend/api/dependencies.py
import hashlib

from fastapi import Depends, Header, HTTPException

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def agent_auth(x_agent_key: str | None = Header(default=None),
               resource=Depends(get_dynamo_resource)) -> str:
    if x_agent_key is None:
        raise HTTPException(status_code=401, detail="Missing X-Agent-Key")

    try:
        tenant_id, key_part = x_agent_key.split(".", 1)
    except ValueError:
        raise HTTPException(status_code=401, detail="Malformed X-Agent-Key")

    agents = AgentsTable(resource).query_by_tenant(tenant_id)
    for agent in agents:
        if agent.get("api_key_hash") == key_part and agent.get("status") == "active":
            return tenant_id
    raise HTTPException(status_code=401, detail="Invalid agent key")
```

`x_agent_key` defaults to `None` (not FastAPI's bare `Header(...)`
required-param shape) so a missing header goes through this function and
returns a consistent 401, not FastAPI's automatic 422 for a missing
required parameter — found while writing the "unauthenticated" test: both
401 and 422 are valid per `docs/api-contract.md`, but mixing "422 for
missing, 401 for malformed/wrong" is an inconsistent auth error taxonomy
worth avoiding on purpose.

- [ ] **Step 2: Implement `api/routes/agent.py`**

```python
# services/backend/api/routes/agent.py
from fastapi import APIRouter, Depends

from services.backend.api.dependencies import agent_auth
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import MitigationStateTable, WhitelistTable
from services.backend.ml.feature_engineering import compute_features_for_ip, record_batch
from services.backend.ml.model import AnomalyTier, ModelManager, classify_score
from services.backend.schemas.mitigation import MitigationState
from services.backend.schemas.telemetry import TelemetryBatch, TelemetryResponse

router = APIRouter()


@router.post("/agent/v1/telemetry", response_model=TelemetryResponse)
def ingest_telemetry(
    batch: TelemetryBatch,
    tenant_id: str = Depends(agent_auth),
    resource=Depends(get_dynamo_resource),
) -> TelemetryResponse:
    if not batch.logs:
        return TelemetryResponse(received=0, processed_ips=0, decisions=[])

    touched_ips = record_batch(resource, tenant_id, batch.logs)
    whitelist = {i["ip"] for i in WhitelistTable(resource).query_by_tenant(tenant_id)}

    mgr = ModelManager()
    mgr.load(resource, tenant_id)  # cached across warm invocations, see Stage 3 —
    # do NOT "simplify" this back to an unconditional registry.load_model() call

    decisions: list[MitigationState] = []
    for ip in touched_ips:
        if ip in whitelist:
            continue
        vector = compute_features_for_ip(resource, tenant_id, ip)
        if vector is None:
            continue
        for v, score in mgr.score_vectors([vector]):
            tier = classify_score(score)
            if tier == AnomalyTier.NORMAL:
                continue
            state = MitigationState(
                ip=v.remote_addr, tier=int(tier), score=score,
                reason="behavioral_anomaly", expires_at=0,
            )
            MitigationStateTable(resource).put(tenant_id=tenant_id, **state.model_dump())
            decisions.append(state)

    return TelemetryResponse(
        received=len(batch.logs), processed_ips=len(touched_ips), decisions=decisions,
    )


@router.get("/agent/v1/decisions", response_model=list[MitigationState])
def list_decisions(
    tenant_id: str = Depends(agent_auth),
    resource=Depends(get_dynamo_resource),
) -> list[MitigationState]:
    items = MitigationStateTable(resource).query_by_tenant(tenant_id)
    return [MitigationState(**item) for item in items]
```

`/agent/v1/register` (from `docs/api-contract.md`) is deliberately NOT
built in this stage — it needs Cognito JWT auth (tenant owner,
interactive), which doesn't exist until Stage 6. Building it now would
mean either faking auth or blocking this stage on Stage 6 — neither is
right, so it's listed as a Stage 6 deliverable instead.

- [ ] **Step 3: Implement `main.py`**

```python
# services/backend/main.py
from fastapi import FastAPI
from mangum import Mangum

from services.backend.api.routes.agent import router as agent_router

app = FastAPI()
app.include_router(agent_router)


@app.get("/health")
def health():
    return {"status": "healthy"}


handler = Mangum(app)
```

- [ ] **Step 4: Add an autouse fixture to `tests/conftest.py` clearing dependency_overrides**

```python
# append to services/backend/tests/conftest.py
@pytest.fixture(autouse=True)
def _clear_fastapi_dependency_overrides():
    """`services.backend.main.app` is a module-level singleton shared by
    every test file that imports it — a test that sets
    app.dependency_overrides[get_dynamo_resource] would leak into every
    later test otherwise."""
    yield
    try:
        from services.backend.main import app
        app.dependency_overrides.clear()
    except ImportError:
        pass  # main.py doesn't exist yet in earlier stages' test runs
```

- [ ] **Step 5: Write the tests**

```python
# services/backend/tests/test_agent_routes.py
from fastapi.testclient import TestClient

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, create_all_tables
from services.backend.main import app


def _client(dynamo_resource):
    create_all_tables(dynamo_resource)
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", registered_at="2026-08-21T00:00:00Z",
        last_seen_at="2026-08-21T00:00:00Z", agent_version="0.1.0",
        api_key_hash="testkeyhash", status="active",
    )
    # The correct FastAPI DI override — swaps the resource for every route
    # declaring Depends(get_dynamo_resource). See the "Design correction"
    # note above this stage's Step 1 for why a hand-rolled global-reassign
    # approach does not work here.
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app)


def test_telemetry_unauthenticated_is_401(dynamo_resource):
    client = _client(dynamo_resource)
    resp = client.post("/agent/v1/telemetry", json={"logs": []})
    assert resp.status_code == 401


def test_telemetry_empty_batch(dynamo_resource):
    client = _client(dynamo_resource)
    resp = client.post("/agent/v1/telemetry", json={"logs": []},
                        headers={"X-Agent-Key": "t-1.testkeyhash"})
    assert resp.status_code == 200
    assert resp.json()["received"] == 0

# Plus: malformed-key/wrong-key 401 tests, a normal-traffic (shadow-mode,
# no decisions) test, a whitelisted-IP-skipped test, and a
# GET /agent/v1/decisions test — 7 tests total. See the checked-in
# services/backend/tests/test_agent_routes.py for the full set; not
# reproduced here to keep this plan from drifting out of sync with a file
# that changes independently of it.
```

- [ ] **Step 6: Run tests, iterate to green, commit**

Run: `cd services/backend && python -m pytest tests/test_agent_routes.py -v`
Expected: PASS (7 tests)

```bash
git add services/backend/main.py services/backend/api/ services/backend/schemas/ \
        services/backend/tests/test_agent_routes.py services/backend/tests/conftest.py
git commit -m "feat(backend): Lambda handler + agent-facing endpoints"
```

**Checkpoint (stage done when):**
`cd services/backend && python -m pytest tests/ -v` passes locally (moto).
Real-AWS checkpoint (run by the developer, not in this environment):
`sam local start-lambda` or an actual `aws lambda invoke` against a
deployed function returns 200 for `GET /health`.

---

## Stage 5 — Usage counters + free-tier ceiling warning (do this before Stage 6, not after)

**Goal:** Protect the 0đ constraint — the single highest-risk assumption in
the whole project. Every Lambda invocation increments `UsageCounters`;
`/admin/v1/usage` reports today's totals against the Always-Free ceilings
(Lambda 1M req / 400,000 GB-s per month; DynamoDB 25 RCU/WCU) so the
publisher gets a warning before a ceiling is hit, not an AWS bill after.

**Files:**
- Create: `services/backend/core/usage.py`
- Modify: `services/backend/main.py:24-27` (add middleware calling `usage.record_invocation`)
- Create: `services/backend/api/routes/admin_usage.py`
- Test: `services/backend/tests/test_usage.py`

**Interfaces:**
- Produces: `record_invocation(resource, estimated_gb_seconds: float) -> None`
  — atomic `UpdateItem ADD` on today's `UsageCounters` item (1 WCU per
  Lambda invocation, well inside the 25 WCU/sec ceiling at expected v1 scale).
- Produces: `get_usage_report(resource, date: str) -> UsageReport` (from
  `docs/api-contract.md`'s schema) with `ceiling_warning: bool` set true
  when `total_requests` crosses 80% of `1_000_000 / 30` (a rough daily
  share of the monthly Lambda ceiling — exact allocation strategy is a
  Phase-3-tunable constant, not re-derived here).

**Design corrections made before/while writing this stage (this plan code
had never been executed):**
1. `record_invocation` must NOT call `resource.Table("UsageCounters").update_item(...)`
   directly — `estimated_gb_seconds` is a float, and a raw `update_item`
   call bypasses `_to_dynamo_safe`, hitting the exact "Float types are not
   supported" error Stage 2 already found and fixed once. Fixed by adding
   a `UsageCountersTable(_SimpleTable)` class (`core/tables.py`) with an
   `add_invocation()` method that goes through the shared `update()`
   helper (Stage 1/2's review fix), same as every other table.
2. The middleware code below originally referenced a module-level
   `_resource` variable from `main.py` — that variable no longer exists
   after Stage 4's redesign (FastAPI `Depends(get_dynamo_resource)`
   replaced it). Middleware runs outside FastAPI's dependency-injection
   call graph, so it does NOT automatically honor
   `app.dependency_overrides` the way a route parameter does; a naive
   `get_dynamo_resource()` call in middleware would hit the real
   (unreachable in tests) resource even when a test has overridden it for
   every route. Fixed with a small `_resolve_resource(request)` helper
   that checks `request.app.dependency_overrides` manually.

- [ ] **Step 1: Add `UsageCountersTable` to `core/tables.py`**

```python
# append to services/backend/core/tables.py
class UsageCountersTable(_SimpleTable):
    _table_name = "UsageCounters"
    _key_names = ("date",)

    def add_invocation(self, date: str, estimated_gb_seconds: float) -> None:
        self.update(
            key={"date": date},
            update_expression="ADD total_requests :one, estimated_gb_seconds :gbs",
            expr_values={":one": 1, ":gbs": estimated_gb_seconds},
        )
```

- [ ] **Step 2: Write the failing test**

```python
# services/backend/tests/test_usage.py
from services.backend.core.tables import UsageCountersTable, create_all_tables
from services.backend.core.usage import _today, get_usage_report, record_invocation

def test_record_invocation_increments_counter(dynamo_resource):
    create_all_tables(dynamo_resource)
    record_invocation(dynamo_resource, estimated_gb_seconds=0.05)
    record_invocation(dynamo_resource, estimated_gb_seconds=0.05)
    report = get_usage_report(dynamo_resource, date=None)  # None = today
    assert report.total_requests == 2
    assert round(report.estimated_gb_seconds, 2) == 0.10

def test_ceiling_warning_flips_true_near_daily_share(dynamo_resource):
    # Seed the counter directly in one write instead of looping ~28,000
    # individual record_invocation() calls (the original version of this
    # test) — that loop is real per-call moto overhead and takes long
    # enough to time out a normal test run. Same threshold logic either way.
    create_all_tables(dynamo_resource)
    daily_share = 1_000_000 / 30
    UsageCountersTable(dynamo_resource).put(
        date=_today(), total_requests=int(daily_share * 0.85), estimated_gb_seconds=0.0,
    )
    report = get_usage_report(dynamo_resource, date=None)
    assert report.ceiling_warning is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/backend && python -m pytest tests/test_usage.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement `core/usage.py`**

```python
# services/backend/core/usage.py
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/backend && python -m pytest tests/test_usage.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Implement `/admin/v1/usage` route**

```python
# services/backend/api/routes/admin_usage.py
from fastapi import APIRouter, Depends

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.usage import UsageReport, get_usage_report

router = APIRouter()


@router.get("/admin/v1/usage", response_model=UsageReport)
def usage(resource=Depends(get_dynamo_resource)) -> UsageReport:
    # NOT auth-gated yet — Cognito admin-group auth is a Stage 6
    # deliverable. Do not expose this route in a real deployment before
    # Stage 6 adds that dependency.
    return get_usage_report(resource, date=None)
```

- [ ] **Step 7: Wire into `main.py`: register the router and add usage-tracking middleware**

```python
# services/backend/main.py — additions
from services.backend.api.routes.admin_usage import router as admin_usage_router
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.usage import record_invocation

app.include_router(admin_usage_router)


def _resolve_resource(request):
    override = request.app.dependency_overrides.get(get_dynamo_resource)
    return override() if override else get_dynamo_resource()


@app.middleware("http")
async def track_usage(request, call_next):
    response = await call_next(request)
    record_invocation(_resolve_resource(request))
    return response
```

Note the ordering: the increment happens AFTER `call_next()` returns, so a
request never sees its own increment in its own response (verified by a
test — `GET /admin/v1/usage` right after one `GET /health` reports 1, not
2). A one-request lag on an approximate ceiling-warning number is
harmless; documented so it isn't mistaken for a bug later.

- [ ] **Step 8: Write route-level tests and commit**

```python
# services/backend/tests/test_admin_usage.py
from fastapi.testclient import TestClient

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import create_all_tables
from services.backend.core.usage import get_usage_report
from services.backend.main import app

def _client(dynamo_resource):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app)

def test_usage_middleware_increments_on_every_request(dynamo_resource):
    client = _client(dynamo_resource)
    client.get("/health")
    client.get("/health")
    report = get_usage_report(dynamo_resource, date=None)
    assert report.total_requests == 2
```

Run: `cd services/backend && python -m pytest tests/test_usage.py tests/test_admin_usage.py -v`
Expected: PASS (6 tests)

```bash
git add services/backend/core/usage.py services/backend/core/tables.py \
        services/backend/api/routes/admin_usage.py services/backend/main.py \
        services/backend/tests/test_usage.py services/backend/tests/test_admin_usage.py
git commit -m "feat(backend): usage counters + free-tier ceiling warning"
```

**Checkpoint (stage done when):**
`cd services/backend && python -m pytest tests/ -v` passes locally (moto),
including the automated middleware-fires-on-every-request test above (no
manual verification needed — it's a real assertion, not a spot check).

---

## Stage 6 — Cognito auth + dashboard/control-platform endpoints

**Goal:** Implement `/dashboard/v1/*` (tenant-scoped) and the remaining
`/admin/v1/*` routes (`tenants`, `agents`, `tenants/{id}/suspend`) from
`docs/api-contract.md`, gated by Cognito JWT.

**Files:**
- Create: `services/backend/core/config.py` (pydantic-settings — Cognito
  user pool id/region/app client id, all with defaults so import never
  fails without a `.env`)
- Create: `services/backend/api/cognito_auth.py`
- Create: `services/backend/api/routes/dashboard.py`
- Create: `services/backend/api/routes/admin.py`
- Create: `services/backend/schemas/whitelist.py`, `model_status.py`, `admin.py`
- Modify: `services/backend/core/tables.py` — `TenantsTable.suspend()`,
  `TenantsTable.list_all()`, `AgentsTable.query_by_status()`; `update()`
  gains an optional `condition_expression` param
- Modify: `services/backend/api/routes/admin_usage.py`, `main.py` — wire
  in `admin_auth`/new routers
- Test: `services/backend/tests/test_cognito_auth.py`,
  `test_dashboard_routes.py`, `test_admin_routes.py`

**Design decision — stronger than what this stage originally sketched:**
the plan's note said tests could use "a fake JWT decoder" for
`dashboard_auth`/`admin_auth`. Implemented instead with a REAL RSA
keypair generated per test (`cognito_test_keys` fixture in `conftest.py`)
and a REAL signed JWT (`sign_test_token`), verified through the exact same
`jwt.decode(...)` call production uses — only the JWKS-fetching function
(`get_jwks`, the one piece that talks to real Cognito) is swapped via
`app.dependency_overrides`. Tests cover a tampered signature and a
wrong-signing-key token, both correctly rejected — a "fake decoder" that
skips real verification couldn't catch either of those.

**Bugs/inconsistencies found and fixed while implementing (not anticipated on paper):**
1. `docs/api-contract.md`'s `WhitelistRequest` has a `reason` field that
   `docs/schema.md`'s `Whitelist` table never defined (only `added_at`/
   `added_by`) — a doc inconsistency between two Phase 2 documents.
   Resolved by storing `reason` as an extra DynamoDB attribute (no fixed
   schema beyond keys) rather than silently dropping it.
2. `TenantsTable.suspend()`: DynamoDB's `UpdateItem` creates the item if
   the key doesn't exist by default. An unconditional version would
   silently create a half-populated tenant record (`status="suspended"`
   and nothing else) when given a bad `tenant_id`, instead of 404ing.
   Fixed with `ConditionExpression="attribute_exists(tenant_id)"` — needed
   extending `_SimpleTable.update()` with an optional
   `condition_expression` param.
3. `GET /admin/v1/agents`: the GSI's partition key IS `status`, so there
   is no single query for "every agent regardless of status" — only
   `query_by_status(status)`. Defaulted the endpoint to `status="stale"`,
   matching `docs/schema.md`'s own stated purpose for this GSI ("list
   stale/dead agents across all tenants without a table scan"), rather
   than pretending a general "list all agents" endpoint works the same way.
4. `GET /admin/v1/tenants` needed a genuine `Scan` (`TenantsTable.list_all()`)
   — `Tenants` has no sort key or GSI to `Query` across. Accepted
   deliberately: an admin-only, low-cardinality table (tenant count), not
   a hot path — unlike `Agents`, which got a real GSI because it needed one.
5. Retrofitted `admin_auth` onto Stage 5's `/admin/v1/usage`, which shipped
   unauthenticated with an explicit "Stage 6 closes this" note — closed here.
6. `ModelStatus.model_ready` triggered a Pydantic v2 warning (`model_`
   is a protected namespace). Fixed with `model_config = ConfigDict(protected_namespaces=())`
   rather than renaming a field name fixed by `docs/api-contract.md`.

**Checkpoint (stage done when):**
`cd services/backend && python -m pytest tests/ -v` — all 56 tests from
Stages 1-6 pass locally (moto + locally-signed JWTs, no real AWS/Cognito).

---

## Stage 7 — Daily retrain via EventBridge

**Goal:** A scheduled Lambda entry point that retrains each tenant's model
from that tenant's accumulated `TelemetryEvents` buckets, using
`services/backend/ml/training.py` from Stage 3.

**Files:**
- Create: `services/backend/retrain_handler.py` (separate Lambda entry
  point, triggered by an EventBridge scheduled rule, NOT the API handler)
- Modify: `services/backend/core/tables.py` — `TelemetryEventsTable` gains
  a `TenantIndex` GSI, `add_aggregate()`'s signature changes to
  `(tenant_id, ip, bucket_start_ts, agg, ttl_seconds)` and now also
  persists `tenant_id`/`ip` as plain attributes, `query_since()` added,
  default `ttl_seconds` raised from 3600 to 90,000
- Modify: `services/backend/ml/feature_engineering.py` — extracts the
  shared `_vector_from_counts()` formula helper, adds
  `collect_training_vectors()`
- Test: `services/backend/tests/test_retrain_handler.py`, extended
  `test_feature_engineering.py`, `test_dynamo_tables.py`

**A real architectural gap found before writing any Stage 7 code, not
anticipated on paper:** gathering a tenant's training data means reading
every `TelemetryEvents` bucket across every IP for that tenant — exactly
the query `TelemetryEventsTable.query_by_tenant()` was made to deliberately
reject in Stage 2 (partition key is a `tenant_id#ip` composite, not
queryable by tenant alone). Stage 7 had no data source to train from
without fixing this. Fixed with a new `TenantIndex` GSI (HASH=`tenant_id`,
RANGE=`bucket_start_ts`), which required also storing `tenant_id`/`ip` as
plain attributes on every bucket item (previously they only existed
embedded inside the `tenant_ip` string). Budgeted at 5 WCU / 2 RCU
(mirrors the base table's own share, since a GSI is written on every base
write) — new account-wide total: 20 WCU / 14 RCU, still under 25/25.

**Second gap found the same way:** `docs/schema.md` always described this
table as backing a ~24h shadow/training window, but Stage 2's actual
default TTL was 3600s (1h) — it would have deleted almost all of a
tenant's telemetry before the once-daily retrain ever ran. Raised the
default to 90,000s (25h, a day plus margin).

**Refactor made to avoid a train/inference drift bug, not just for
style:** the 7 feature formulas (request_rate, error_ratio, ...) existed
only inside `compute_features_for_ip()`'s weighted blend. Training needs
the *same* formulas applied to *unweighted* historical buckets (each
bucket is one independent sample — the boundary-evasion weighting existed
to protect one live decision, not to describe past data). Extracted into
`_vector_from_counts()`, used by both `compute_features_for_ip()` and the
new `collect_training_vectors()` — duplicating this math would risk
training and inference silently computing different things for the same
field names, a correctness bug class specific to ML systems.

**Deliberate simplification, flagged not hidden:** `retrain_all_tenants()`
trains straight to `stage="production"` with no staging/validation gate.
ADR-002's reuse table listed `ai_engine/ml/validator.py` (block-rate
threshold + std regression checks against the previous production model)
as reuse-candidate material, but porting it is real scope beyond this
stage's own goal — tracked as a backlog item for a future stage, not
silently dropped or forgotten.

**Task-level scope:**
- `retrain_all_tenants(resource) -> dict[str, ModelMetadata | None]` loops
  `TenantsTable.list_all()`, calls `collect_training_vectors()` +
  `train_and_save(..., stage="production")` per tenant (`n_estimators=50`
  per ADR-002), `None` for tenants below `MIN_TRAINING_SAMPLES`.
- v1 is a serial loop — **explicitly acceptable for v1** given the small
  expected tenant count; logs total elapsed time and warns past 600s
  (~2/3 of Lambda's 15-minute timeout) so the fan-out threshold (one
  invocation per tenant via EventBridge/Step Functions) is observable,
  not guessed at.
- `handler(event, context)` — the actual Lambda entry point. Documented,
  not assumed: this is a SEPARATE Lambda function from `main.py`'s API
  handler, so `ModelManager`'s warm-container cache (Stage 3) can't be
  invalidated from here — no shared memory between separate Lambda
  functions. A freshly promoted model reaches API traffic only once the
  API Lambda's own warm containers recycle naturally.

**Checkpoint (stage done when):**
`cd services/backend && python -m pytest tests/ -v` — all 62 tests from
Stages 1-7 pass locally (moto), including a multi-tenant fixture
confirming each tenant gets its own independently trained `Models` item.

---

## Stage 8 — Agent (thin client) + CLI

**Goal:** Build the two components that don't exist in the old codebase at
all (PRD US-3, US-5) — a sklearn-free process the tenant runs next to their
own service, and a CLI to register it.

**Enforcer design — resolved with the developer before writing code, and
made stronger than the two hard-coded options originally sketched here.**
Asked which single mechanism to use (nginx config rewrite vs. iptables vs.
agent-as-proxy); the developer pushed back that a single rigid mechanism
isn't good enough and asked for research into what comparable tools do
and a genuinely better design, not just a pick from those three.

Prior art surveyed: **fail2ban** (tails logs, on threshold runs one
configured "action" — one mechanism per install) and its modern successor
**CrowdSec** (separates detection from enforcement via pluggable
"bouncers" — nginx-bouncer, iptables-bouncer, Cloudflare-bouncer, etc. —
so the same detection engine can drive different local enforcement
depending on what's available, and multiple bouncers can run at once).
CrowdSec's bouncer-plugin architecture is the relevant improvement over
fail2ban's single-hook design; adapted here as:

- `services/agent/enforcer/base.py` — an `EnforcementAdapter` ABC
  (`is_available()`, `block(ip, tier, expires_at)`, `unblock(ip)`).
- `services/agent/enforcer/nginx_adapter.py` — tier 2 (HARD_BLOCK) writes
  `deny <ip>;` lines to one include file; tier 1 (RATE_LIMIT) writes a
  `geo $binary_remote_addr $agent_rate_limited { ... }` map to a second
  include file — the standard nginx pattern for a live-updatable per-IP
  soft limit (the tenant's own `nginx.conf` is expected to define a
  `limit_req_zone` keyed on `$agent_rate_limited`). Reloads nginx after
  every change via an injectable command runner (never shells out for
  real in tests).
- `services/agent/enforcer/iptables_adapter.py` — OS-level `iptables ...
  DROP`, works regardless of what app/proxy stack the tenant runs. Only
  supports tier 2: a proportional soft rate-limit isn't cleanly
  expressible with plain iptables (would need the less-portable
  `hashlimit` module) — `block()` returns `False` for tier 1 rather than
  pretending to support it.
- `services/agent/enforcer/__init__.py::detect_adapters()` — auto-detects
  which adapters can actually run in the current environment and keeps
  ALL of them, not exactly one. This is the actual improvement over both
  fail2ban (one hook) and a naive three-way choice (still one mechanism,
  just a different one): nginx AND iptables can both be active
  simultaneously (defense in depth), with no per-install configuration
  required — the agent works out of the box across more environments.
- `services/agent/enforcer/__init__.py::DecisionStore` — tracks active
  decisions and their `expires_at`, sweeping and calling `unblock()` once
  a TTL passes. Neither `deny` nor an iptables DROP rule expires on its
  own; something has to actively remove it later.

**Real bugs found and fixed while building this, not anticipated on
paper:**
1. `MitigationStateTable` decisions had `expires_at=0` hardcoded since
   Stage 4 — an agent enforcer has no TTL to schedule an unblock from
   without a real value. Fixed in `api/routes/agent.py`: tier-dependent
   TTLs (`RATE_LIMIT` 300s, `HARD_BLOCK` 3600s), `expires_at = now + ttl`.
2. `agent_auth` (Stage 4) compared the raw `X-Agent-Key` value directly
   against the stored `api_key_hash` — `hash_api_key()` existed but was
   never called. Every test up to Stage 7 passed only because they
   hand-set `api_key_hash` to the same literal string sent as the "key",
   masking that a real registered agent's actual raw key could never
   equal its own hash. Caught by building `/agent/v1/register` and
   testing the real register→authenticate flow end-to-end for the first
   time, not by testing either piece in isolation.

**Files:**
- `services/agent/requirements.txt` — `click` only, no scikit-learn/
  pandas/numpy/requests. HTTP calls use stdlib `urllib.request`
  (`services/agent/http_client.py`) rather than adding a dependency —
  genuinely dependency-light, not just "no ML libraries."
- `services/agent/config.py` — reads/writes `~/.aiops-agent/config.json`
  (0600 permissions, best-effort — it holds the raw `api_key`).
- `services/agent/collector.py` — `Collector`/`LogRecord`: batches by size
  OR time interval (whichever first), POSTs to `/agent/v1/telemetry` with
  `X-Agent-Key: <tenant_id>.<api_key>`. No ML, just forwarding — keeps the
  agent genuinely thin per ADR-002.
- `services/agent/cli.py` — Click; `agent register --backend-url <url>
  --token <cognito-id-token> --label <name>` calls the new
  `POST /agent/v1/register` (built in this stage, in
  `services/backend/api/routes/agent.py`, Cognito-`dashboard_auth`-gated)
  and saves the response to config. **Known v1 gap, flagged not hidden:**
  `--token` expects a Cognito ID token pasted from the dashboard login —
  a full CLI OAuth device-code flow is out of scope for this stage.
- Test: `services/agent/tests/test_{config,http_client,collector,enforcer,cli}.py`
  — 29 tests, all passing on first run against locally-injected fakes (no
  real nginx/iptables/network calls in any test).

**Checkpoint (stage done when):**
`cd services/agent && python -m pytest tests/ -v` — 29/29 pass.
`cd services/backend && python -m pytest tests/ -v` — 93/93 pass
(includes the register→authenticate end-to-end test and the TTL-expiry
test added alongside the two bug fixes above).

---

## Stage 9 — Dashboard + Control Platform UI

**Goal:** Serve the two human-facing UIs (PRD US-6, US-7) as HTML/JS
directly from the Lambda Function URL, per ADR-002's UI decision.

**Stack decision, made with the developer at this stage's start:** the
developer had no strong opinion and asked for a researched, well-designed
default rather than a guess. Presented two no-build-step options (Jinja2 +
plain fetch() JS, vs Jinja2 + htmx); the developer instead asked for
genuinely good UI/UX and full, real coverage of every existing capability,
deferring the technical pick. Chose **Jinja2 (FastAPI's built-in template
support) + a small custom attribute-driven JS helper**, not the real htmx
library — this offline dev environment cannot fetch and verify the actual
htmx.js, and reproducing a well-tested third-party library from memory
risked shipping something that silently isn't what it claims to be.
`services/backend/ui/static/interactions.js` (~70 lines, no dependency, no
build step) implements only the small `hx-get`/`hx-post`/`hx-delete`/
`hx-target`/`hx-trigger`/`hx-confirm` subset the two pages actually use.

**A real gap found and fixed while designing this stage, not anticipated
on paper:** `dashboard_auth`/`admin_auth` (Stage 6) only ever read a
Bearer token from the `Authorization` header — correct for JSON API
clients, but a plain HTML page navigation (typing a URL, clicking a link,
a native form GET/POST) cannot attach a custom header, only JS/fetch can.
Without a fix, server-rendered pages could never authenticate via normal
browser navigation. Fixed in `services/backend/api/cognito_auth.py`:
`dashboard_auth`/`admin_auth` now accept an `id_token` cookie as a
fallback when no `Authorization` header is present — both paths still
funnel into the exact same `_decode_and_verify`, so verification itself
didn't change, only where the token is read from.

**"Không vượt quá sự kiểm soát" / "phủ hết toàn bộ dự án" (the
developer's stated bar):** the dashboard renders ONLY by calling
`dashboard_auth`-gated logic (tenant-scoped by construction, tested
explicitly — `test_dashboard_shows_own_tenant_mitigations_only` asserts
another tenant's IP never appears); the Control Platform surfaces every
`/admin/v1/*` capability that exists — tenants (+ suspend), agents (status
filter via the `LastSeenIndex` GSI), and the usage/ceiling-warning banner
— nothing invented beyond what the backend actually does, and nothing
left out that it does.

**Files:**
- `services/backend/ui/templates/` — `base.html` (design system: CSS
  custom properties for color/spacing, no external fonts/CDN — a
  self-contained system-font stack), `app_shell.html` (sidebar+topbar
  layout, shared by both UIs), `login.html`, `dashboard.html`,
  `control_platform.html`, plus `_whitelist_table.html`/
  `_tenants_table.html`/`_agents_table.html` partials returned directly by
  the `hx-*`-driven endpoints for in-place updates without a full reload.
- `services/backend/ui/templates_env.py` — the shared `Jinja2Templates` instance.
- `services/backend/ui/auth_pages.py` — `/ui/login` (GET renders the form,
  POST verifies the pasted token and sets the `id_token` cookie, routing
  to `/dashboard/ui` or `/admin/ui` based on the token's claims),
  `/ui/logout`, and the static route serving `interactions.js`.
- `services/backend/ui/dashboard.py` — `/dashboard/ui` (full page) and the
  `hx-post`/`hx-delete` whitelist endpoints, all reusing
  `api/routes/dashboard.py`'s existing functions directly (calling a
  `Depends(...)`-decorated function with explicit keyword args works the
  same as FastAPI resolving it) rather than re-querying DynamoDB a second way.
- `services/backend/ui/control_platform.py` — `/admin/ui` (full page),
  `/admin/ui/agents` (status-filter partial), `/admin/ui/tenants/{id}/suspend`
  — same reuse pattern against `api/routes/admin.py`.
- `services/backend/main.py` — registers the three new routers, plus an
  `HTTPException` handler scoped to `/dashboard/ui`/`/admin/ui` paths only:
  a 401/403 there redirects to `/ui/login` (or, for the JS helper's AJAX
  calls, returns an `X-UI-Redirect` header) instead of the JSON API's
  default error body — a human looking at a web page should never see a
  raw `{"detail": ...}` blob. Every other route (`/agent/v1/*`,
  `/dashboard/v1/*`, `/admin/v1/*`) is untouched by this handler.
- Test: `test_ui_auth_pages.py`, `test_ui_dashboard.py`,
  `test_ui_control_platform.py`, plus 2 new cookie-fallback tests in
  `test_cognito_auth.py` — 25 new tests, 116/116 total across
  `services/backend` + `services/agent`.

**Known v1 gap, flagged not hidden:** login is "paste your Cognito ID
token" (same shortcut as the CLI's `--token` flag) — a real Cognito
Hosted UI OAuth redirect flow is future work, not built in this stage.

**Checkpoint (stage done when):**
`cd services/backend && python -m pytest tests/ -v` — 116/116 pass
(combined with `services/agent`). A logged-in tenant can see their own
active mitigations and edit their whitelist end-to-end (verified: another
tenant's data never appears); a logged-in admin can see all tenants,
filter agents by status, suspend a tenant, and see today's usage-ceiling
status end-to-end — all exercised by real `TestClient` requests against
real Jinja2-rendered HTML, not just the underlying JSON API.

---

## Self-review notes (written against this plan, not a separate document)

- **PRD coverage:** US-1/US-2 [Done, old model] → superseded by Stages 2-3
  (feature computation + ML core, ported and adapted). US-3 (agent) →
  Stage 8. US-4 (backend) → Stages 1-5, 7. US-5 (CLI) → Stage 8. US-6
  (dashboard) → Stage 6 + 9. US-7 (control platform) → Stages 5, 6, 9.
  US-8 (security review) → intentionally NOT a stage here, per PRD's own
  note: run via `/sdlc use 4` once this plan is implemented, not before.
  US-9 (real-AWS verification) → the "real-AWS checkpoint" notes on Stages
  4 and 7 exist for this; full verification is the developer's to run,
  same constraint as the old PLAN.md's Stage 3/6/7.
- **Highest-risk item surfaced early, not late:** Stage 2 (telemetry
  aggregation under the 25 WCU/RCU ceiling) and Stage 5 (usage-ceiling
  warning) both come before the dashboard/UI polish work, per the
  developer's explicit priority for this session.
- **Known open decisions, resolved when their stage started, not
  guessed ahead of time:** Stage 8's enforcer mechanism (resolved as a
  pluggable nginx+iptables adapter architecture, CrowdSec-inspired) and
  Stage 9's UI stack (resolved as Jinja2 + a small custom JS helper) were
  both left open in this plan until the developer weighed in at the start
  of their respective stage, per the interaction contract for genuinely
  open architecture questions.
- **Hardening pass (2026-08-21), found and fixed before first approval:**
  reviewing this plan against its own 0đ constraint surfaced four real
  weaknesses, all fixed in the stages above, not just noted: (1) Stage 1's
  table spec used `PAY_PER_REQUEST` billing, which has no Always-Free
  allowance at all and would have billed from request one — changed to
  `PROVISIONED` with an explicit ≤25 RCU/25 WCU account-wide budget; (2)
  Stage 2's first draft wrote to DynamoDB once per raw log line and grew
  `uris`/`user_agents` lists without bound — both defeated the stage's own
  purpose and got more expensive exactly during a real attack; fixed by
  aggregating in Lambda memory first (1 write/IP/batch) and switching to
  fixed-size numeric fields only; (3) Stage 4's telemetry handler would
  have read the ~238KB model item on every request (≈60 RCU, over budget
  in a single call) — fixed with a module-level cache in `ModelManager`
  across warm Lambda invocations; (4) a single fixed time bucket let an
  attacker split volume across the bucket boundary to evade detection —
  fixed with a two-bucket weighted sliding-window counter (Stage 2).
- **Residual risk, documented rather than solved (no Always-Free AWS
  primitive fully closes it):** the public Lambda Function URL has no
  built-in request throttling once API Gateway (which had it) was dropped
  for cost reasons (ADR-002). An anonymous flood of unauthenticated
  requests still consumes Lambda's request-count quota and a small amount
  of `Agents` table RCU on the auth check before failing with 401. This is
  bounded, not catastrophic — Lambda's own per-request pricing beyond the
  free tier is fractions of a cent per thousand requests, not the "vài đô
  bất ngờ" the developer explicitly ruled out earlier — and Stage 5's
  `UsageCounters`/ceiling-warning is the intended early-warning mechanism.
  A stronger fix (CloudFront, WAF) is NOT Always-Free and is out of scope
  under the confirmed constraint; if this risk becomes unacceptable later,
  that trade-off needs a fresh conversation with the developer, not a
  silent architecture change here.
