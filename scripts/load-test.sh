#!/bin/bash
set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

TARGET_URL="${1:-http://localhost:30080}"
DURATION="${LOAD_TEST_DURATION:-300}"
CONNECTIONS="${LOAD_TEST_CONNECTIONS:-10}"
THREADS="${LOAD_TEST_THREADS:-2}"
RPS="${LOAD_TEST_RPS:-20}"

log_step "Load Test Configuration"
log_info "Target URL   : $TARGET_URL"
log_info "Duration     : ${DURATION}s"
log_info "Connections  : $CONNECTIONS"
log_info "Threads      : $THREADS"
log_info "Target RPS   : $RPS"

if ! command -v wrk > /dev/null 2>&1; then
    log_error "wrk not found. Install with: sudo apt-get install wrk"
    exit 1
fi

log_step "Phase 1/3 Warm up (60s, low traffic)"
wrk -t2 -c5 -d60s     --latency     "$TARGET_URL/"     2>&1 | tail -20

log_step "Phase 2/3 Normal load (${DURATION}s)"
wrk -t"$THREADS"     -c"$CONNECTIONS"     -d"${DURATION}s"     --latency     "$TARGET_URL/"     2>&1 | tail -20

log_step "Phase 3/3 Mixed endpoints"
for ENDPOINT in / /health /api/test /login; do
    log_info "Testing endpoint: $ENDPOINT"
    wrk -t2 -c10 -d30s         --latency         "$TARGET_URL$ENDPOINT"         2>&1 | tail -5
done

log_info "Load test completed"
log_info "Check Grafana dashboard for feature vectors and model activity"
