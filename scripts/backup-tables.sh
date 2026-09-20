#!/bin/bash
# Export / restore the three tables that cannot be reconstructed.
#
#   bash scripts/backup-tables.sh export ~/aiops-backups
#   bash scripts/backup-tables.sh restore ~/aiops-backups/20260824-2200
#
# WHY THIS EXISTS INSTEAD OF POINT-IN-TIME RECOVERY
# PITR and on-demand backups are billed per GB and are not in the Always-Free
# tier, so enabling them breaks the constraint that shaped this architecture
# (ADR-002). A Scan of three small tables consumes read capacity that is
# already provisioned and already paid for, and the output lands wherever you
# keep it. The AWS cost of running this is genuinely zero.
#
# WHAT IT DOES AND DOES NOT PROTECT
# DynamoDB already replicates synchronously across three availability zones, so
# hardware loss was never the exposure. The exposures are accidental deletion,
# malicious deletion, and a bug overwriting rows — and against those, the
# Terraform side does most of the work for free:
#   - deletion_protection_enabled on every table
#   - prevent_destroy on these three
#   - the API role has no DeleteItem on Tenants or Agents at all
# This script covers what those cannot: rows deleted or corrupted through a
# legitimate code path.
#
# THE HONEST LIMIT: it is manual. Whatever changed since the last export is
# gone. For three tables that change when a tenant signs up or edits a
# whitelist, running it after each such change is realistic; pretending an
# unattended cron would be free is not.
#
# THE DUMP IS SENSITIVE. It contains agent API key hashes and customers'
# whitelisted IP addresses. Store it the way you would store a password
# manager export — not in the project repository.
set -euo pipefail

# Resolve a Python that actually runs. `python3` EXISTS on a Windows machine
# with Git Bash and is a Microsoft Store stub that prints an advert and exits
# non-zero, so `command -v python3` is not a usable test - the interpreter has
# to be probed. Found running this script for real on 2026-09-21.
PY=""
for candidate in python3 python py; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "ERROR: no working Python found (tried python3, python, py)." >&2
  echo "       This script needs one to chunk BatchWriteItem into the 25-item" >&2
  echo "       groups DynamoDB accepts." >&2
  exit 1
fi

# Under Git Bash, paths look like /c/Users/... but a WINDOWS Python resolves
# them literally and fails with FileNotFoundError on a file the shell just
# wrote. cygpath is the translator; it exists only where the problem does.
native_path() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s' "$1"
  fi
}

TABLES="Tenants Agents Whitelist"
MODE="${1:?usage: backup-tables.sh export <dir> | restore <dir>}"
DIR="${2:?usage: backup-tables.sh export <dir> | restore <dir>}"
REGION="${AWS_REGION:-ap-southeast-1}"

case "$MODE" in
  export)
    STAMP=$(date -u +%Y%m%d-%H%M)
    OUT="$DIR/$STAMP"
    mkdir -p "$OUT"
    for t in $TABLES; do
      # --consistent-read: a backup taken from a stale replica is a backup of
      # something that may never have existed.
      aws dynamodb scan --table-name "$t" --region "$REGION" \
        --consistent-read --output json > "$OUT/$t.json"
      # Read the count out of the JSON with grep rather than Python: it keeps
      # the export path free of any interpreter, and the field is written by
      # the AWS CLI in a fixed shape.
      COUNT=$(grep -o '"Count"[[:space:]]*:[[:space:]]*[0-9]*' "$OUT/$t.json" | head -1 | grep -o '[0-9]*$')
      echo "  exported $t: $COUNT items"
    done
    echo "written to $OUT"
    echo "REMINDER: this dump holds API key hashes and customer IPs. Store it accordingly."
    ;;

  restore)
    [ -d "$DIR" ] || { echo "no such export: $DIR" >&2; exit 1; }
    echo "This OVERWRITES current rows with the exported ones. Items created"
    echo "since the export are NOT removed - restore is a merge, not a rewind."
    read -r -p "Type the export directory name to confirm: " CONFIRM
    [ "$CONFIRM" = "$(basename "$DIR")" ] || { echo "aborted" >&2; exit 1; }

    for t in $TABLES; do
      [ -f "$DIR/$t.json" ] || { echo "  skip $t: not in this export"; continue; }
      # BatchWriteItem takes 25 items per call, so chunk. Written in python
      # rather than jq because python is already a dependency of this project
      # and jq is not.
      "$PY" - "$(native_path "$DIR/$t.json")" "$t" "$REGION" <<'PY'
import json, subprocess, sys
path, table, region = sys.argv[1], sys.argv[2], sys.argv[3]
items = json.load(open(path))["Items"]
for i in range(0, len(items), 25):
    chunk = items[i:i + 25]
    payload = {table: [{"PutRequest": {"Item": it}} for it in chunk]}
    subprocess.run(
        ["aws", "dynamodb", "batch-write-item", "--region", region,
         "--request-items", json.dumps(payload)],
        check=True, stdout=subprocess.DEVNULL)
print(f"  restored {table}: {len(items)} items")
PY
    done
    ;;

  *)
    echo "unknown mode: $MODE (expected export or restore)" >&2
    exit 1
    ;;
esac
