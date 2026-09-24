import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)


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
# TenantIndex GSI), leaving 11 RCU and 5 WCU of headroom under 25/25.
# Writes are the scarce dimension; reads have room.
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
    # The product's memory. Everything else in this schema describes the
    # present: MitigationState is keyed (tenant_id, ip) so a repeat decision
    # overwrites, TTL deletes what survives, and TelemetryEvents keeps 25
    # hours sized to the training window. Nothing retained what the product
    # had DONE, which is why PRD US-6's "view recent mitigation history" has
    # been unmeetable since the schema was written.
    #
    # One table, three item types by sort-key prefix (mit# / agg# / read#).
    # Separate tables would cost two provisioning floors for data that
    # shares a partition key, a TTL policy and every read path.
    #
    # 2 WCU: the ingest quota hard-caps the whole account at ~0.39 batches
    # per second (core/usage.py), so the unconditional rollup write is
    # ~0.39 WCU sustained. Episode writes scale with anomalous IPs, which
    # the same quota bounds. No GSI - a GSI mirrors every write against the
    # same 25-unit account pool, and every access pattern here is a
    # sort-key range on tenant_id.
    {"TableName": "TenantHistory", "KeySchema": [
        {"AttributeName": "tenant_id", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"}],
     "AttributeDefinitions": [
        {"AttributeName": "tenant_id", "AttributeType": "S"},
        {"AttributeName": "sk", "AttributeType": "S"}],
     "ProvisionedThroughput": {"ReadCapacityUnits": 2, "WriteCapacityUnits": 2}},
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

    # DynamoDB truncates EVERY Query at 1MB and signals more with
    # LastEvaluatedKey. Nothing in this codebase followed it: a grep for
    # LastEvaluatedKey/ExclusiveStartKey across services/backend returned
    # zero hits, so every paged read silently returned its first page.
    #
    # It mattered in exactly one place and it mattered a lot. The nightly
    # retrain calls query_since(tenant_id, 0) over 25 hours of 5-second
    # telemetry buckets — megabytes for an active tenant — and a Query
    # returns ascending, so the model was trained on the OLDEST ~5,000
    # buckets in the window, every night, and reported success. Tests
    # passed because moto does not enforce the page limit.
    #
    # The cap is a safety valve, not a page size: a runaway follow-the-cursor
    # loop would burn the whole Lambda timeout and take the request with it,
    # which is a worse failure than truncation. 200 pages is far more than a
    # legitimate 25-hour window needs and still terminates.
    MAX_QUERY_PAGES = 200

    def _query_all_pages(self, **kwargs) -> list[dict]:
        items: list[dict] = []
        for _ in range(self.MAX_QUERY_PAGES):
            resp = self._table.query(**kwargs)
            items.extend(resp.get("Items", []))
            cursor = resp.get("LastEvaluatedKey")
            if not cursor:
                break
            kwargs["ExclusiveStartKey"] = cursor
        else:
            logger.warning("%s: query hit the %d-page cap; results are truncated",
                           self._table_name, self.MAX_QUERY_PAGES)
        return items

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

    def set_threshold(self, tenant_id: str, which: str, z: float) -> float | None:
        """Move one of this tenant's gates, returning the previous value.

        The previous value comes back rather than being looked up again by
        the caller, because the audit row needs both and a second GetItem to
        learn what you just overwrote is a read already paid for.

        On Tenants rather than Models: the nightly retrain rewrites the model
        item, so a threshold living only there would be silently reverted
        every night by the very function that trains on it.

        Conditional on the tenant existing, so a deleted or mistyped tenant
        id fails loudly instead of creating a row that is nothing but a
        threshold.
        """
        resp = self._table.update_item(
            Key={"tenant_id": tenant_id},
            UpdateExpression="SET #k = :z",
            ExpressionAttributeNames={"#k": which},
            ExpressionAttributeValues={":z": _to_dynamo_safe(float(z))},
            ConditionExpression="attribute_exists(tenant_id)",
            ReturnValues="UPDATED_OLD",
        )
        old = resp.get("Attributes", {}).get(which)
        return float(old) if old is not None else None

    def suspend(self, tenant_id: str) -> bool:
        return self._set_status_if_exists(tenant_id, "suspended")

    def create(self, tenant_id: str, **fields) -> bool:
        """Create, never overwrite. Returns False if the id is taken.

        `put()` would silently replace an existing tenant, which for this
        table means detaching every agent and every history row from the
        owner they belong to. The condition is what makes the endpoint
        safely retryable: a double-submitted form collides here and the UI
        renders "already created" rather than making a second tenant.
        """
        try:
            self._table.put_item(
                Item=_to_dynamo_safe({"tenant_id": tenant_id, **fields}),
                ConditionExpression="attribute_not_exists(tenant_id)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def reactivate(self, tenant_id: str) -> bool:
        """Only the tenant's status. Agent keys revoked by suspend() stay
        revoked: the reason for a suspension may be a leaked key, and giving
        that key its access back would undo the suspension's only real
        effect. A reactivated tenant registers fresh agents."""
        return self._set_status_if_exists(tenant_id, "active")

    def _set_status_if_exists(self, tenant_id: str, status: str) -> bool:
        """Returns False (not True/raise) if the tenant doesn't exist —
        callers turn that into a 404. Uses a ConditionExpression rather
        than a plain update_item: DynamoDB's UpdateItem CREATES the item
        if the key doesn't already exist, so an unconditional version
        would silently create a new tenant record containing only a status
        when given a bad tenant_id, instead of failing. suspend() fell into
        exactly that once; reactivate() shares this path so it cannot."""
        try:
            self.update(
                key={"tenant_id": tenant_id},
                update_expression="SET #s = :s",
                expr_names={"#s": "status"},
                expr_values={":s": status},
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

    # An agent is "stale" once it has gone this long without an
    # authenticated call. Five minutes: long enough that a restart or a
    # network blip does not raise an alarm, short enough that a dead agent
    # is visible before the traffic it was supposed to be watching matters.
    STALE_AFTER_SECONDS = 300

    def query_by_status(self, status: str) -> list[dict]:
        """Queries the LastSeenIndex GSI (provisioned in Stage 1) —
        cross-tenant, unlike query_by_tenant(). `status` here is the
        LIFECYCLE state, "active" or "revoked", which is what agent_auth
        checks. It is NOT liveness: see query_live/query_stale, which slice
        the active agents by the index's sort key."""
        from boto3.dynamodb.conditions import Key
        resp = self._table.query(
            IndexName="LastSeenIndex",
            KeyConditionExpression=Key("status").eq(status),
        )
        return resp.get("Items", [])

    def _cutoff(self, now: datetime | None) -> str:
        now = now or datetime.now(timezone.utc)
        return (now - timedelta(seconds=self.STALE_AFTER_SECONDS)).isoformat()

    def query_live(self, now: datetime | None = None) -> list[dict]:
        """Active agents that have called in recently.

        This is the query LastSeenIndex was designed for and never received:
        its sort key is `last_seen_at`, so a range condition answers
        "which agents are alive" across every tenant in one query, with no
        scan and no client-side filtering."""
        from boto3.dynamodb.conditions import Key
        resp = self._table.query(
            IndexName="LastSeenIndex",
            KeyConditionExpression=(Key("status").eq("active")
                                    & Key("last_seen_at").gte(self._cutoff(now))),
        )
        return resp.get("Items", [])

    def query_stale(self, now: datetime | None = None) -> list[dict]:
        """Active agents that have gone quiet — the fleet's actual alarm
        list. Revoked agents are excluded by the partition key: revocation
        is a decision the operator already took, not a fault to chase."""
        from boto3.dynamodb.conditions import Key
        resp = self._table.query(
            IndexName="LastSeenIndex",
            KeyConditionExpression=(Key("status").eq("active")
                                    & Key("last_seen_at").lt(self._cutoff(now))),
        )
        return resp.get("Items", [])

    def query_revoked(self) -> list[dict]:
        return self.query_by_status("revoked")

    def touch(self, tenant_id: str, agent_id: str, now: datetime | None = None) -> bool:
        """Record that this agent just called in. Returns whether a write
        happened.

        Conditional on purpose. `last_seen_at` is the LastSeenIndex sort
        key, so every update is a GSI delete-and-reinsert — about 2 WCU
        against the 25 WCU that the Always-Free tier grants the whole
        account, across every table (see the budget at the top of this
        file). An agent posting telemetry every few seconds would spend
        that allowance on a timestamp whose only consumer is a dashboard
        refreshed by a human. Refusing the write is the normal outcome.

        `attribute_exists(tenant_id)` keeps an UpdateItem from creating a
        malformed record for an agent that was deleted mid-request —
        DynamoDB upserts by default."""
        now = now or datetime.now(timezone.utc)
        try:
            self._table.update_item(
                Key={"tenant_id": tenant_id, "agent_id": agent_id},
                UpdateExpression="SET last_seen_at = :now",
                ConditionExpression=("attribute_exists(tenant_id) "
                                     "AND last_seen_at < :cutoff"),
                ExpressionAttributeValues={":now": now.isoformat(),
                                           ":cutoff": self._cutoff(now)},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def revoke_all_for_tenant(self, tenant_id: str) -> int:
        """Phase 4 / H3: suspending a tenant must invalidate the API keys
        already issued to its agents, not just stop future ones. Returns how
        many agents were revoked. A revoked agent fails agent_auth's
        status=="active" check, so its key is dead immediately — no waiting
        for a token to expire (agent keys never expire on their own)."""
        revoked = 0
        for agent in self.query_by_tenant(tenant_id):
            # Skip agents that are already dead. Counting them made the
            # returned number describe the table rather than this suspension
            # (production reported 2 revoked when one agent was live), and
            # rewriting them spent a write per dead agent for nothing.
            if agent.get("status") == "revoked":
                continue
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

    def query_active(self, tenant_id: str, now: int | None = None) -> list[dict]:
        """Rows whose enforcement is still in force.

        DynamoDB TTL deletes lazily — AWS documents "typically within 48
        hours" of the expiry time, not at it — so an expired row stays
        queryable long after it stopped applying. A 300-second rate limit
        was being listed under the word "Active" for up to two days. The
        filter is applied client-side on purpose: the rows are already in
        memory from the tenant query, so it costs no extra read capacity,
        and a FilterExpression would not have saved any either.

        `expires_at` of 0 means "no expiry set" and is kept, rather than
        being read as the epoch and treated as long past.
        """
        now = now if now is not None else int(time.time())
        return [r for r in self.query_by_tenant(tenant_id)
                if not r.get("expires_at") or int(r["expires_at"]) > now]


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

    def mark_flagged(self, tenant_id: str, ip: str, bucket_start_ts: int) -> None:
        """Record that this bucket was judged hostile, so the nightly retrain
        skips it.

        Without this the attacker trains the model. Measured on production and
        reproduced in test_training_poisoning.py: with three copies of an
        attack in the training window, IsolationForest stops finding it
        unusual - three identical points are a small cluster, and a cluster is
        not an outlier. Attack three times and the fourth goes unnoticed.

        Conditional, like TenantsTable: UpdateItem CREATES a missing item, and
        a flag on a bucket that no longer exists (TTL, or a wrong id) would
        otherwise invent an empty telemetry row for training to read."""
        try:
            self.update(
                key={"tenant_ip": f"{tenant_id}#{ip}", "bucket_start_ts": bucket_start_ts},
                update_expression="SET flagged = :t",
                expr_values={":t": True},
                condition_expression="attribute_exists(tenant_ip)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    # 25 hours of 5-second buckets is ~18,000 items, and this writes one
    # UpdateItem per bucket plus a GSI mirror, synchronously inside one
    # request. Unbounded, that is a guaranteed Lambda timeout against a
    # table provisioned at 5 WCU. 720 is the last hour of buckets, which
    # is where the poisoning lives; the rest ages out of the window on
    # its own.
    FLAG_LIMIT = 720

    def flag_all_for_ip(self, tenant_id: str, ip: str,
                        limit: int | None = None) -> int:
        """Flag every bucket this IP has, so the next retrain ignores all of
        it. Returns how many were flagged.

        The recovery lever for a model that is already poisoned. Flagging at
        decision time stops an attacker teaching the model, but it cannot undo
        it: a poisoned model no longer detects the attack, so it no longer
        flags it, so the next retrain learns it again. Observed on production -
        an attack scoring -0.204 returned no decision at all two days later.

        The exact inverse of the whitelist. The whitelist exempts an IP from
        MITIGATION; this exempts one from TRAINING, and touches nothing else:
        the IP keeps being scored and blocked like any other.

        Bounded, newest first. `last_flag_truncated` says whether there were
        more — a caller undoing model poisoning needs to know it did not
        finish, and silently doing 720 of 18,000 would look like success."""
        limit = self.FLAG_LIMIT if limit is None else limit
        buckets = sorted(self.query_buckets_for_ip(tenant_id, ip),
                         key=lambda b: int(b["bucket_start_ts"]), reverse=True)
        self.last_flag_truncated = len(buckets) > limit
        flagged = 0
        for item in buckets[:limit]:
            self.update(
                key={"tenant_ip": f"{tenant_id}#{ip}",
                     "bucket_start_ts": int(item["bucket_start_ts"])},
                update_expression="SET flagged = :t",
                expr_values={":t": True},
            )
            flagged += 1
        return flagged

    def query_buckets_for_ip(self, tenant_id: str, ip: str) -> list[dict]:
        """Every bucket for one (tenant, IP) - the base table's own partition
        key, so no index and no scan."""
        from boto3.dynamodb.conditions import Key
        return self._query_all_pages(
            KeyConditionExpression=Key("tenant_ip").eq(f"{tenant_id}#{ip}"))

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
        return self._query_all_pages(
            IndexName="TenantIndex",
            KeyConditionExpression=Key("tenant_id").eq(tenant_id) & Key("bucket_start_ts").gte(since_ts),
        )

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

    # Everything the console shows, and nothing that weighs 238 KB.
    _METADATA_FIELDS = (
        "tenant_id", "stage_version", "version", "trained_at",
        "training_samples", "contamination", "score_mean", "score_std",
        "feature_means", "feature_stds", "features", "stage",
        "tier1_z", "tier2_z",
    )

    def get_metadata(self, tenant_id: str,
                     stage_version: str = "production") -> dict | None:
        """The model item without its blob: ~30 RCU down to ~0.5.

        A bare get() pulls the serialised IsolationForest - about 238 KB - to
        print a version string, on a table provisioned at 2 RCU. It is the
        worst read-to-value ratio in the product and it sits on a page a
        customer is invited to open. Same projection pattern as
        registry.model_exists.

        Every field is aliased rather than guessing which are reserved words
        (`stage` is one). A wrong guess is a 400 at request time on a page
        someone opened, not a failure at deploy.
        """
        names = {"#f%d" % i: f for i, f in enumerate(self._METADATA_FIELDS)}
        resp = self._table.get_item(
            Key={"tenant_id": tenant_id, "stage_version": stage_version},
            ProjectionExpression=", ".join(names),
            ExpressionAttributeNames=names,
        )
        return resp.get("Item")

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

    def get_many(self, keys: list[str]) -> dict[str, dict]:
        """Several counter rows in one round trip.

        BatchGetItem bills the same RCU as the same number of GetItems and
        costs one network call instead of N. The publisher's tenant page
        issues one GetItem per tenant today; this is the shape that replaces
        it, and it is what makes "am I protected right now" affordable on
        every page rather than one.
        """
        if not keys:
            return {}
        resp = self._table.meta.client.batch_get_item(RequestItems={
            self._table.name: {"Keys": [{"date": k} for k in keys]},
        })
        rows = resp.get("Responses", {}).get(self._table.name, [])
        return {row["date"]: row for row in rows}

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


class TenantHistoryTable(_SimpleTable):
    """Mitigation episodes, hourly traffic rollups and the unread marker.

    Sort keys zero-pad the epoch to 10 digits so lexicographic ordering
    equals chronological ordering; unpadded epochs sort wrong as soon as the
    digit count changes, and 10 digits lasts until 2286.
    """

    _table_name = "TenantHistory"
    _key_names = ("tenant_id", "sk")

    RETENTION_SECONDS = 30 * 86_400
    HOUR = 3600

    # --- keys -------------------------------------------------------------

    @staticmethod
    def episode_sk(hour_start: int, ip: str) -> str:
        return f"mit#{int(hour_start):010d}#{ip}"

    @staticmethod
    def series_sk(hour_start: int) -> str:
        return f"agg#{int(hour_start):010d}"

    @staticmethod
    def setting_sk(now: int, nonce: str = "") -> str:
        """A fourth prefix on the same single-table design.

        No new table, no provisioning floor, no GSI, and it reads through the
        existing between() path. `agg#` < `mit#` < `set#` lexically, so the
        three range reads cannot see each other's rows - a property the tests
        assert rather than assume.

        `nonce` is what makes the ledger genuinely append-only. The key was
        the timestamp alone, at one-second resolution, so two audited actions
        in the same second were one PutItem overwriting the other and the log
        silently lost a record - allowing a source and removing it again in
        the same second left only the removal.

        It has to SORT, not merely differ. A random suffix separates the two
        rows and then returns them in random order within the second, which
        for a log is its own kind of wrong: "allowed, then removed" and
        "removed, then allowed" are different events. So the nonce leads with
        a fixed-width numeric sub-second field taken from the SAME instant as
        the second above it - read separately from the clock it would drift
        against it and invert the pair it exists to order - and ordinary
        lexical ordering on the sort key is then chronological ordering. The
        short random tail only breaks a genuine tie, where two events share a
        timestamp to the nanosecond and there is no fact of the matter about
        which came first.

        Written without a nonce when the key is being used as a RANGE BOUND
        rather than as a row key.
        """
        return f"set#{int(now):010d}" + (f"#{nonce}" if nonce else "")

    @classmethod
    def hour_of(cls, ts: int) -> int:
        return int(ts) // cls.HOUR * cls.HOUR

    @staticmethod
    def max_tier(episode: dict) -> int:
        """Derived, not stored. DynamoDB update expressions have no MAX, and
        per-tier counters carry strictly more information for the same one
        write than a single max value would."""
        return 2 if int(episode.get("tier2_count", 0)) else 1

    # --- writes -----------------------------------------------------------

    def record_decision(self, tenant_id: str, ip: str, hour_start: int, tier: int,
                        now: int, score: float, z: float | None,
                        features: list[float] | None = None,
                        stats_version: str | None = None) -> None:
        """One idempotent UpdateItem, no read and no condition.

        Hourly rather than per-decision: MitigationState.put fires on every
        scoring pass for an already-blocked IP and the agent flushes every
        five seconds, so per-decision rows would reach ~720 per attacking IP
        per hour. This collapses them into the thing a person actually wants
        to read.

        Routed through update() rather than a bare update_item because
        `score` and `z` are floats and only update() applies
        _to_dynamo_safe - the "Float types are not supported" error this
        codebase has already hit twice.
        """
        tier = int(tier)

        # Bytes on a write that already happens: ~184 B to ~284 B, still one
        # 1 KB unit, still 1 WCU, zero extra operations. The version id
        # travels with the vector because `z` is frozen at decision time
        # while the baseline is read live — without it, one nightly retrain
        # makes the two halves of the explanation describe different models
        # and nothing on the screen would say so.
        #
        # This row is a fixed-size numeric record and must stay one. It fires
        # once per IP per hour, so a single variable-length text field big
        # enough to cross 1 KB would double the write cost of a 3,000-IP hour
        # against a 2-WCU table.
        evidence = ""
        extra: dict = {}
        if features:
            evidence += ", last_features = :f"
            extra[":f"] = [float(x) for x in features]
        if stats_version:
            evidence += ", stats_version = :sv"
            extra[":sv"] = stats_version

        self.update(
            key={"tenant_id": tenant_id, "sk": self.episode_sk(hour_start, ip)},
            update_expression=(
                "ADD tier1_count :t1, tier2_count :t2 "
                "SET last_ts = :now, ip = :ip, hour_start = :h, "
                "last_score = :s, last_z = :z" + evidence + ", "
                "first_ts = if_not_exists(first_ts, :now), #ttl = :ttl"
            ),
            expr_names={"#ttl": "ttl"},
            expr_values={
                ":t1": 1 if tier == 1 else 0,
                ":t2": 1 if tier >= 2 else 0,
                ":now": int(now), ":ip": ip, ":h": int(hour_start),
                ":s": score, ":z": z,
                ":ttl": int(hour_start) + self.RETENTION_SECONDS,
                **extra,
            },
        )

    # Twelve bins of 0.25 sigma from 3.0, plus one overflow at 6.0 matching
    # SIGMA_CEILING in ui/charts.py so the data and the chart agree. The
    # floor is 3.0 on product grounds, not byte grounds: ADR-006 measured
    # 0.82% false positives at 3.5 sigma and the rate climbs steeply below
    # it, so the gate control must not offer a setting it cannot honestly
    # recommend.
    #
    # Names are `n` plus sigma x 100: 4 B of name, 4 B of value, 8 B a bin.
    # The agg# item is ~119 B with 905 B spare, so thirteen bins cost 104 B
    # and the row stays inside one 1 KB write unit.
    NEAR_BINS: tuple[float, ...] = tuple(3.0 + 0.25 * i for i in range(13))

    @classmethod
    def bin_name(cls, magnitude: float) -> str | None:
        """Which bin a |z| falls in, or None below the floor.

        Takes a MAGNITUDE. z is negative by convention, and a sign slip would
        file every source in the lowest bin without complaining, so a
        negative argument raises rather than being helpfully absolved.
        """
        if magnitude < 0:
            raise ValueError("bin_name takes |z|, not z")
        if magnitude < cls.NEAR_BINS[0]:
            return None
        edge = max(b for b in cls.NEAR_BINS if b <= magnitude)
        return "n%d" % round(edge * 100)

    def record_traffic(self, tenant_id: str, hour_start: int, requests: int,
                       tier1: int = 0, tier2: int = 0,
                       bins: dict[str, int] | None = None) -> None:
        """One atomic ADD per telemetry batch - a constant, independent of
        how many IPs the batch touched. Fixed-size scalars only: anything
        that grows with request volume is the trap add_aggregate was written
        to avoid.

        `bins` rides in the same ADD, so the near-threshold histogram that
        makes the gate previewable in BOTH directions costs zero extra
        operations and zero extra WCU. Without it only raising a gate can be
        backtested: a source below 4 sigma leaves no trace anywhere, so the
        sources a lower gate would newly catch were never written down.

        Only bins the batch actually hit are emitted - ADD creates a missing
        numeric attribute, so a bin never hit costs nothing. The read side
        must zero-fill, because an absent bin comes back absent rather than
        0, exactly as query_series(fill=True) already handles empty hours.
        """
        adds = ["requests :r", "batches :b",
                "tier1_decisions :t1", "tier2_decisions :t2"]
        values = {":r": int(requests), ":b": 1,
                  ":t1": int(tier1), ":t2": int(tier2),
                  ":h": int(hour_start),
                  ":ttl": int(hour_start) + self.RETENTION_SECONDS}
        for i, (name, count) in enumerate(sorted((bins or {}).items())):
            adds.append("%s :nb%d" % (name, i))
            values[":nb%d" % i] = int(count)

        self.update(
            key={"tenant_id": tenant_id, "sk": self.series_sk(hour_start)},
            update_expression=("ADD " + ", ".join(adds) +
                               " SET hour_start = :h, #ttl = :ttl"),
            expr_names={"#ttl": "ttl"},
            expr_values=values,
        )

    def mark_read(self, tenant_id: str, through_ts: int) -> None:
        """Never moves backwards: two tabs, or a retried request, must not
        re-announce what the customer has already seen."""
        try:
            self.update(
                key={"tenant_id": tenant_id, "sk": "read#"},
                update_expression="SET last_read_ts = :t",
                condition_expression=("attribute_not_exists(sk) "
                                      "OR last_read_ts < :t"),
                expr_values={":t": int(through_ts)},
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    # --- reads ------------------------------------------------------------

    def record_setting(self, tenant_id: str, actor: str, what: str,
                       old, new, now: float, because: str | None = None) -> None:
        """One PutItem per change: ~200 B, 1 WCU, 30-day TTL.

        Append-only and never updated — the point of the row is that it
        records a thing that happened, so a later change writes a second row
        rather than editing this one.

        Only four actions in this product earn one: a threshold move, a
        whitelist add or remove, a tenant suspend or reactivate, and an agent
        key mint or revoke. The test is whether a reasonable person could
        later dispute it with money, blame or security attached. Page views,
        filters and theme changes do not qualify, and none of the four gets an
        undo: all four already have an inverse action.

        `actor` comes from a verified JWT claim, never from a request field.
        That is what keeps this row fixed-size, which is the same rule the
        episode row follows for the same reason.

        `because` is the evidence line an appeal was made from - one of the
        seven feature names, checked against that list by the caller before
        it reaches here. Bounded and short by construction, so the row stays
        the fixed size the rule above demands. Absent rather than empty when
        no single line explains it: a stored "" would later read as a claim
        that the operator was asked and declined to say.
        """
        extra = {"because": because} if because else {}
        # `now` is a float here on purpose. Callers pass time.time(); the row
        # keeps whole seconds and the sort key keeps the rest, so two changes
        # inside one second order correctly instead of colliding.
        second = int(now)
        sub = int(round((float(now) - second) * 1_000_000_000))
        self.put(
            tenant_id=tenant_id,
            sk=self.setting_sk(second, f"{sub:09d}{secrets.token_hex(2)}"),
            actor=actor,
            what=what,
            old=old,
            new=new,
            at=second,
            ttl=second + self.RETENTION_SECONDS,
            **extra,
        )

    def query_settings(self, tenant_id: str, since: int, until: int) -> list[dict]:
        from boto3.dynamodb.conditions import Key
        return self._query_all_pages(
            KeyConditionExpression=(
                Key("tenant_id").eq(tenant_id)
                # "~" (0x7E) sorts above the "#" (0x23) that opens the
                # nonce, so the bound reaches every row written in the final
                # second. A bare setting_sk(until) bound drops them, which is
                # the same trap query_episodes documents on its own key.
                & Key("sk").between(self.setting_sk(since),
                                    self.setting_sk(until) + "~")
            ),
        )

    def query_episodes(self, tenant_id: str, since_ts: int, until_ts: int) -> list[dict]:
        """Newest first - this is a log, and a person reads the top of it.

        The upper bound reaches a whole hour past `until_ts` because the IP
        suffix sorts after the hour and "#" (0x23) sorts below the digits;
        a naive "mit#{until}#" bound silently drops every episode in the
        final hour, which is the one the customer opened the page for.
        """
        from boto3.dynamodb.conditions import Key
        lo = f"mit#{self.hour_of(since_ts):010d}#"
        hi = f"mit#{self.hour_of(until_ts) + self.HOUR:010d}#"
        return self._query_all_pages(
            KeyConditionExpression=(Key("tenant_id").eq(tenant_id)
                                    & Key("sk").between(lo, hi)),
            ScanIndexForward=False,
        )

    def query_series(self, tenant_id: str, since_ts: int, until_ts: int,
                     fill: bool = False) -> list[dict]:
        """Oldest first - a chart reads left to right."""
        from boto3.dynamodb.conditions import Key
        lo = self.series_sk(self.hour_of(since_ts))
        hi = self.series_sk(self.hour_of(until_ts)) + "~"
        rows = self._query_all_pages(
            KeyConditionExpression=(Key("tenant_id").eq(tenant_id)
                                    & Key("sk").between(lo, hi)),
        )
        if not fill:
            return rows
        # DynamoDB has no row for an hour in which nothing happened. A chart
        # that simply skips those hours draws a flat line across an outage
        # instead of a hole, so the gap is filled here, once, server-side,
        # rather than in each caller.
        by_hour = {int(r["hour_start"]): r for r in rows}
        out = []
        for hour in range(self.hour_of(since_ts),
                          self.hour_of(until_ts) + self.HOUR, self.HOUR):
            out.append(by_hour.get(hour, {
                "hour_start": hour, "requests": 0, "batches": 0,
                "tier1_decisions": 0, "tier2_decisions": 0,
            }))
        return out

    def unread_since(self, tenant_id: str, default_ts: int) -> int:
        """`default_ts` matters: an absent marker means the tenant has never
        looked, and treating that as epoch 0 would present a month of
        history as new the first time the feature speaks."""
        item = self.get(tenant_id=tenant_id, sk="read#")
        if not item or "last_read_ts" not in item:
            return int(default_ts)
        return int(item["last_read_ts"])
