# The seven tables, mirroring services/backend/core/tables.py::_TABLE_SPECS
# exactly. Key names, types and capacity must match that list — the application
# was written against these shapes and a drift here fails at runtime, not at
# plan time.
#
# CAPACITY BUDGET (ADR-002: the account's Always-Free pool is 25 RCU / 25 WCU
# TOTAL, and GSI throughput bills separately against the same pool):
#
#   table              RCU  WCU
#   Tenants              1    1
#   Agents               1    1   + LastSeenIndex   1 / 1
#   Whitelist            1    1
#   MitigationState      3    3
#   Models               2    1
#   TelemetryEvents      2    5   + TenantIndex     2 / 5
#   UsageCounters        1    2
#   ------------------------------
#   TOTAL               14   20    of 25 / 25
#
# Five RCU and five WCU of headroom. Spend them deliberately.

resource "aws_dynamodb_table" "tenants" {
  name           = "Tenants"
  billing_mode   = "PROVISIONED"
  read_capacity  = 1
  write_capacity = 1
  hash_key       = "tenant_id"

  attribute {
    name = "tenant_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "agents" {
  name           = "Agents"
  billing_mode   = "PROVISIONED"
  read_capacity  = 1
  write_capacity = 1
  hash_key       = "tenant_id"
  range_key      = "agent_id"

  attribute {
    name = "tenant_id"
    type = "S"
  }
  attribute {
    name = "agent_id"
    type = "S"
  }
  attribute {
    name = "status"
    type = "S"
  }
  attribute {
    name = "last_seen_at"
    type = "S"
  }

  # Cross-tenant agent health for the Control Platform. status is the partition
  # key, which is why /admin/v1/agents queries one status at a time rather than
  # "all agents" — see docs/api-contract.md.
  global_secondary_index {
    name            = "LastSeenIndex"
    hash_key        = "status"
    range_key       = "last_seen_at"
    projection_type = "ALL"
    read_capacity   = 1
    write_capacity  = 1
  }
}

resource "aws_dynamodb_table" "whitelist" {
  name           = "Whitelist"
  billing_mode   = "PROVISIONED"
  read_capacity  = 1
  write_capacity = 1
  hash_key       = "tenant_id"
  range_key      = "ip"

  attribute {
    name = "tenant_id"
    type = "S"
  }
  attribute {
    name = "ip"
    type = "S"
  }
}

resource "aws_dynamodb_table" "mitigation_state" {
  name           = "MitigationState"
  billing_mode   = "PROVISIONED"
  read_capacity  = 3
  write_capacity = 3
  hash_key       = "tenant_id"
  range_key      = "ip"

  attribute {
    name = "tenant_id"
    type = "S"
  }
  attribute {
    name = "ip"
    type = "S"
  }

  # PHASE 7 FIX. docs/schema.md has always described expires_at as "the table's
  # native DynamoDB TTL attribute (free auto-expiry, replaces Redis key TTL)",
  # and the application writes it on every decision — but nothing ever enabled
  # TTL on the table. moto does not expire items either, so 164 passing tests
  # could not have caught it. Without this block, mitigation rows accumulate
  # forever server-side even though the agent expires them locally.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "models" {
  name           = "Models"
  billing_mode   = "PROVISIONED"
  read_capacity  = 2
  write_capacity = 1
  hash_key       = "tenant_id"
  range_key      = "stage_version"

  attribute {
    name = "tenant_id"
    type = "S"
  }
  attribute {
    name = "stage_version"
    type = "S"
  }
}

resource "aws_dynamodb_table" "telemetry_events" {
  name           = "TelemetryEvents"
  billing_mode   = "PROVISIONED"
  read_capacity  = 2
  write_capacity = 5
  hash_key       = "tenant_ip"
  range_key      = "bucket_start_ts"

  attribute {
    name = "tenant_ip"
    type = "S"
  }
  attribute {
    name = "bucket_start_ts"
    type = "N"
  }
  attribute {
    name = "tenant_id"
    type = "S"
  }

  # Added in Stage 7 so the retrain job can read a tenant's buckets across every
  # IP — the base table's tenant_id#ip composite key cannot be queried by tenant
  # alone. GSI writes mirror base-table writes, hence the matching WCU.
  global_secondary_index {
    name            = "TenantIndex"
    hash_key        = "tenant_id"
    range_key       = "bucket_start_ts"
    projection_type = "ALL"
    read_capacity   = 2
    write_capacity  = 5
  }

  # PHASE 7 FIX, same omission as MitigationState above and more serious here:
  # this is the highest-write table in the system, one item per unique IP per
  # 5-second bucket. Without TTL it grows without bound and takes the storage
  # free tier with it. The application writes `ttl` = bucket_start_ts + 90,000
  # (25h, sized to cover the daily retrain window).
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "usage_counters" {
  name           = "UsageCounters"
  billing_mode   = "PROVISIONED"
  read_capacity  = 1
  write_capacity = 2
  hash_key       = "date"

  attribute {
    name = "date"
    type = "S"
  }
}

locals {
  all_tables = [
    aws_dynamodb_table.tenants,
    aws_dynamodb_table.agents,
    aws_dynamodb_table.whitelist,
    aws_dynamodb_table.mitigation_state,
    aws_dynamodb_table.models,
    aws_dynamodb_table.telemetry_events,
    aws_dynamodb_table.usage_counters,
  ]
  table_arns = [for t in local.all_tables : t.arn]
  # GSIs are separate ARNs for IAM purposes; a policy granting Query on the
  # table does NOT cover its indexes.
  index_arns = [for t in local.all_tables : "${t.arn}/index/*"]
}
