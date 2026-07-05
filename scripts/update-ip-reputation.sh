#!/bin/bash
set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
REPUTATION_KEY="reputation:blacklist"
TMP_DIR="/tmp/ip-reputation"
MERGED_FILE="$TMP_DIR/merged-blacklist.txt"

mkdir -p "$TMP_DIR"

log_step "1/4 Download public blocklists"

log_info "Downloading Firehol Level 1"
curl -fsSL     "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset"     -o "$TMP_DIR/firehol_level1.txt" 2>/dev/null ||     log_error "Failed to download Firehol Level 1, skipping"

log_info "Downloading Emerging Threats compromised IPs"
curl -fsSL     "https://rules.emergingthreats.net/blockrules/compromised-ips.txt"     -o "$TMP_DIR/emerging_threats.txt" 2>/dev/null ||     log_error "Failed to download Emerging Threats, skipping"

log_info "Downloading Spamhaus DROP list"
curl -fsSL     "https://www.spamhaus.org/drop/drop.txt"     -o "$TMP_DIR/spamhaus_drop.txt" 2>/dev/null ||     log_error "Failed to download Spamhaus DROP, skipping"

log_step "2/4 Merge and clean blocklists"
cat "$TMP_DIR"/*.txt 2>/dev/null     | grep -v "^#"     | grep -v "^;"     | grep -v "^$"     | grep -E "^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]"     | awk '{print $1}'     | sort -u     > "$MERGED_FILE"

TOTAL=$(wc -l < "$MERGED_FILE")
log_info "Total unique IPs/CIDRs: $TOTAL"

log_step "3/4 Load into Redis"
LOADED=0
BATCH_SIZE=1000

while IFS= read -r ip || [ -n "$ip" ]; do
    [ -z "$ip" ] && continue
    redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT"         SADD "$REPUTATION_KEY" "$ip" > /dev/null
    LOADED=$((LOADED + 1))
done < "$MERGED_FILE"

redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT"     EXPIRE "$REPUTATION_KEY" 172800 > /dev/null

log_info "Loaded $LOADED entries into Redis key: $REPUTATION_KEY"

log_step "4/4 Cleanup temp files"
rm -rf "$TMP_DIR"

log_info "IP reputation update completed: $LOADED entries loaded"
