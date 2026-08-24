from decimal import Decimal

from botocore.exceptions import ClientError


def _to_dynamo_safe(value):
    """boto3's DynamoDB resource API rejects native Python float ('Float
    types are not supported. Use Decimal types instead.') — found by
    actually running Stage 2's tests against moto, not anticipated in the
    hand-written plan. Converts via str() to avoid binary-float artifacts
    (e.g. Decimal(0.05) != Decimal("0.05")). Applied recursively so every
    table that stores a float (this one's total_time, later ones' score/
    contamination/etc.) is safe without repeating this at every call site."""
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
# Budget below sums to 20 WCU / 14 RCU (Stage 7 added TelemetryEvents'
# TenantIndex GSI), leaving headroom under 25/25.
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
        {"AttributeName": "bucket_start_ts", "AttributeType": "N"},
        {"AttributeName": "tenant_id", "AttributeType": "S"}],
     # Highest-write table by design (every unique IP per telemetry batch) —
     # gets the largest share of the WCU budget.
     "ProvisionedThroughput": {"ReadCapacityUnits": 2, "WriteCapacityUnits": 5},
     "GlobalSecondaryIndexes": [{
        "IndexName": "TenantIndex",
        "KeySchema": [
            {"AttributeName": "tenant_id", "KeyType": "HASH"},
            {"AttributeName": "bucket_start_ts", "KeyType": "RANGE"}],
        "Projection": {"ProjectionType": "ALL"},
        # Added in Stage 7: gathering a tenant's training data means
        # reading every bucket item across every IP for that tenant, which
        # the base table's tenant_ip#ip composite key can't Query by
        # tenant alone (see TelemetryEventsTable.query_by_tenant's
        # deliberate NotImplementedError, Stage 2). GSI writes mirror
        # every base-table write, so this roughly matches the base
        # table's own WCU share.
        "ProvisionedThroughput": {"ReadCapacityUnits": 2, "WriteCapacityUnits": 5},
     }]},
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
               expr_names: dict | None = None, expr_values: dict | None = None,
               condition_expression: str | None = None) -> None:
        """Shared wrapper for update_item calls. Unlike put(), a bare
        `self._table.update_item(...)` call does NOT go through
        _to_dynamo_safe — found during review: TelemetryEventsTable's
        original add_aggregate() had to convert its one float field by
        hand, which meant the next float field added via update_item
        elsewhere (e.g. Stage 5's UsageCounters) would silently hit the
        same 'Float types are not supported' error again. Routing every
        update_item call through this method makes the fix apply
        automatically everywhere, not just at the one call site it was
        first noticed at."""
        kwargs = {"Key": key, "UpdateExpression": update_expression}
        if expr_names:
            kwargs["ExpressionAttributeNames"] = expr_names
        if expr_values:
            kwargs["ExpressionAttributeValues"] = _to_dynamo_safe(expr_values)
        if condition_expression:
            kwargs["ConditionExpression"] = condition_expression
        self._table.update_item(**kwargs)

    def query_by_tenant(self, tenant_id: str) -> list[dict]:
        """Queries by this table's partition key — meaningful only for
        tables whose partition key IS a plain tenant_id (Tenants, Agents,
        Whitelist, MitigationState, Models). Uses self._key_names[0]
        rather than a hardcoded "tenant_id" string, found during review:
        the hardcoded version silently returned zero rows (well-formed but
        wrong) or would raise a DynamoDB ValidationException on any table
        whose partition key is named differently (see
        TelemetryEventsTable's override, which rejects this call outright
        instead of returning a misleading empty/wrong result)."""
        from boto3.dynamodb.conditions import Key
        partition_key = self._key_names[0]
        resp = self._table.query(KeyConditionExpression=Key(partition_key).eq(tenant_id))
        return resp.get("Items", [])


class TenantsTable(_SimpleTable):
    _table_name = "Tenants"
    _key_names = ("tenant_id",)

    def suspend(self, tenant_id: str) -> bool:
        """Returns False (not True/raise) if the tenant doesn't exist —
        callers turn that into a 404. Uses a ConditionExpression rather
        than a plain update_item: DynamoDB's UpdateItem CREATES the item
        if the key doesn't already exist, so an unconditional version
        would silently create a new tenant record containing only
        status='suspended' when given a bad tenant_id, instead of failing."""
        try:
            self.update(
                key={"tenant_id": tenant_id},
                update_expression="SET #s = :s",
                expr_names={"#s": "status"},
                expr_values={":s": "suspended"},
                condition_expression="attribute_exists(tenant_id)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def list_all(self) -> list[dict]:
        """A full table Scan — the only option for "every tenant" since
        Tenants has no sort key or GSI to Query across. Acceptable here:
        an admin-only, cross-tenant listing on a table sized in tenants
        (dozens to low hundreds for this project's scale), not per-request
        hot-path traffic like Agents (which got a real GSI instead,
        query_by_status(), because it needed one)."""
        return self._table.scan().get("Items", [])


class AgentsTable(_SimpleTable):
    _table_name = "Agents"
    _key_names = ("tenant_id", "agent_id")

    def query_by_status(self, status: str) -> list[dict]:
        """Queries the LastSeenIndex GSI (provisioned in Stage 1) —
        cross-tenant, unlike query_by_tenant(). Matches schema.md's stated
        purpose for this GSI: letting the Control Platform list e.g. all
        'stale' agents across every tenant without a full table scan."""
        from boto3.dynamodb.conditions import Key
        resp = self._table.query(
            IndexName="LastSeenIndex",
            KeyConditionExpression=Key("status").eq(status),
        )
        return resp.get("Items", [])

    def revoke_all_for_tenant(self, tenant_id: str) -> int:
        """Phase 4 / H3: suspending a tenant must invalidate the API keys
        already issued to its agents, not just stop future ones. Returns how
        many agents were revoked. A revoked agent fails agent_auth's
        status=="active" check, so its key is dead immediately — no waiting
        for a token to expire (agent keys never expire on their own)."""
        revoked = 0
        for agent in self.query_by_tenant(tenant_id):
            self.update(
                key={"tenant_id": tenant_id, "agent_id": agent["agent_id"]},
                update_expression="SET #s = :s",
                expr_names={"#s": "status"},
                expr_values={":s": "revoked"},
            )
            revoked += 1
        return revoked


class WhitelistTable(_SimpleTable):
    _table_name = "Whitelist"
    _key_names = ("tenant_id", "ip")


class MitigationStateTable(_SimpleTable):
    _table_name = "MitigationState"
    _key_names = ("tenant_id", "ip")


class TelemetryEventsTable(_SimpleTable):
    _table_name = "TelemetryEvents"
    _key_names = ("tenant_ip", "bucket_start_ts")

    def add_aggregate(self, tenant_id: str, ip: str, bucket_start_ts: int, agg: dict,
                       ttl_seconds: int = 90_000) -> None:
        """Single atomic write of an ALREADY-AGGREGATED batch summary for
        one (tenant, ip, bucket) — never called per log line. `agg` keys:
        request_count, error_count, post_count, total_bytes, total_time,
        distinct_uri_count, distinct_ua_count — all plain numbers, so the
        item stays a fixed handful of bytes regardless of traffic volume
        (no lists, nothing that grows with request count).

        `ttl_seconds` default changed from 3600 (1h) to 90,000 (25h) in
        Stage 7: docs/schema.md always said this table backs a ~24h
        shadow/training window, but the original 1h TTL would have
        deleted almost all of a tenant's telemetry before the daily
        retrain ever ran — found while designing the retrain job's data
        source, before writing it.

        Also writes `tenant_id`/`ip` as their own plain attributes (not
        just embedded in the `tenant_ip` composite key) — needed for the
        `TenantIndex` GSI added in Stage 7, so a tenant's training data
        can be queried without parsing the composite key string."""
        tenant_ip = f"{tenant_id}#{ip}"
        self.update(
            key={"tenant_ip": tenant_ip, "bucket_start_ts": bucket_start_ts},
            update_expression=(
                "ADD request_count :rc, error_count :ec, post_count :pc, "
                "total_bytes :tb, total_time :tt, "
                "distinct_uri_count :du, distinct_ua_count :da "
                "SET #ttl = :ttl, tenant_id = :tid, ip = :ip"
            ),
            expr_names={"#ttl": "ttl"},
            expr_values={
                ":rc": agg["request_count"], ":ec": agg["error_count"],
                ":pc": agg["post_count"], ":tb": agg["total_bytes"],
                ":tt": agg["total_time"], ":du": agg["distinct_uri_count"],
                ":da": agg["distinct_ua_count"],
                ":ttl": bucket_start_ts + ttl_seconds,
                ":tid": tenant_id, ":ip": ip,
            },
        )

    def get_bucket(self, tenant_ip: str, bucket_start_ts: int) -> dict | None:
        return self.get(tenant_ip=tenant_ip, bucket_start_ts=bucket_start_ts)

    def query_since(self, tenant_id: str, since_ts: int = 0) -> list[dict]:
        """All bucket items for a tenant (every IP) at or after `since_ts`,
        via the TenantIndex GSI — used by the daily retrain job (Stage 7)
        to gather that tenant's training data. Unlike query_by_tenant()
        (deliberately unimplemented on this table, Stage 2), this queries
        a real index built for exactly this cross-IP, per-tenant access
        pattern, not the base table's composite key."""
        from boto3.dynamodb.conditions import Key
        resp = self._table.query(
            IndexName="TenantIndex",
            KeyConditionExpression=Key("tenant_id").eq(tenant_id) & Key("bucket_start_ts").gte(since_ts),
        )
        return resp.get("Items", [])

    def get_buckets_batch(self, tenant_ip: str, bucket_starts: list[int]) -> dict[int, dict]:
        """Fetch multiple buckets for the same tenant_ip in one DynamoDB
        BatchGetItem call instead of one GetItem per bucket — found during
        review: compute_features_for_ip() was issuing two independent
        sequential GetItem calls (current bucket, previous bucket) that
        don't depend on each other's result. Same total RCU cost, fewer
        round trips on the hot telemetry-scoring path. Retries any
        UnprocessedKeys (DynamoDB may partially fail a batch under
        throttling) up to 3 times before giving up on the remainder."""
        if not bucket_starts:
            return {}

        pending = [{"tenant_ip": tenant_ip, "bucket_start_ts": ts} for ts in bucket_starts]
        found: dict[int, dict] = {}

        for _ in range(3):
            if not pending:
                break
            resp = self._resource.batch_get_item(
                RequestItems={self._table_name: {"Keys": pending}}
            )
            for item in resp.get("Responses", {}).get(self._table_name, []):
                found[int(item["bucket_start_ts"])] = item
            pending = resp.get("UnprocessedKeys", {}).get(self._table_name, {}).get("Keys", [])

        return found

    def query_by_tenant(self, tenant_id: str) -> list[dict]:
        # This table's partition key is "{tenant_id}#{ip}", a composite —
        # NOT a plain tenant_id — so it can't be queried by tenant alone
        # (DynamoDB Query only supports begins_with on a SORT key, not the
        # partition key). Found during review: the inherited base
        # implementation would have silently queried the wrong attribute
        # and returned an empty list instead of failing loudly. Raising
        # here is deliberate — a caller needing "all telemetry for tenant
        # X" needs a GSI on tenant_id, which doesn't exist yet.
        raise NotImplementedError(
            "TelemetryEventsTable's partition key is a tenant_id#ip composite, "
            "not a plain tenant_id — query_by_tenant() doesn't apply here. "
            "Use get_bucket(tenant_ip, bucket_start_ts) for a specific IP/bucket."
        )


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


class UsageCountersTable(_SimpleTable):
    _table_name = "UsageCounters"
    _key_names = ("date",)

    def add_invocation(self, date: str, estimated_gb_seconds: float) -> None:
        """Atomic ADD via the shared `update()` helper — NOT a raw
        `resource.Table(...).update_item(...)` call. `estimated_gb_seconds`
        is a float; without routing through `update()` (which applies
        `_to_dynamo_safe`), this hits the exact 'Float types are not
        supported' error Stage 2 already found and fixed once — found on
        review before writing any Stage 5 code, since the first draft of
        this method used update_item directly."""
        self.update(
            key={"date": date},
            update_expression="ADD total_requests :one, estimated_gb_seconds :gbs",
            expr_values={":one": 1, ":gbs": estimated_gb_seconds},
        )
