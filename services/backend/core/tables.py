from botocore.exceptions import ClientError

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
        self._table = resource.Table(self._table_name)

    def put(self, **item) -> None:
        self._table.put_item(Item=item)

    def get(self, **key) -> dict | None:
        resp = self._table.get_item(Key={k: key[k] for k in self._key_names})
        return resp.get("Item")

    def delete(self, **key) -> None:
        self._table.delete_item(Key={k: key[k] for k in self._key_names})

    def query_by_tenant(self, tenant_id: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key
        resp = self._table.query(KeyConditionExpression=Key("tenant_id").eq(tenant_id))
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
        self._table.update_item(
            Key={"tenant_ip": tenant_ip, "bucket_start_ts": bucket_start_ts},
            UpdateExpression=(
                "ADD request_count :rc, error_count :ec, post_count :pc, "
                "total_bytes :tb, total_time :tt, "
                "distinct_uri_count :du, distinct_ua_count :da "
                "SET #ttl = :ttl"
            ),
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={
                ":rc": agg["request_count"], ":ec": agg["error_count"],
                ":pc": agg["post_count"], ":tb": agg["total_bytes"],
                ":tt": agg["total_time"], ":du": agg["distinct_uri_count"],
                ":da": agg["distinct_ua_count"],
                ":ttl": bucket_start_ts + ttl_seconds,
            },
        )

    def get_bucket(self, tenant_ip: str, bucket_start_ts: int) -> dict | None:
        return self.get(tenant_ip=tenant_ip, bucket_start_ts=bucket_start_ts)


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
