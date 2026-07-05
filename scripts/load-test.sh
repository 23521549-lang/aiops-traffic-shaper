#!/bin/bash
set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }
log_warn()  { echo "$LOG_PREFIX [WARN]  $*"; }

TARGET_URL="${1:-http://localhost:30080}"
DURATION="${LOAD_TEST_DURATION:-300}"
CONNECTIONS="${LOAD_TEST_CONNECTIONS:-10}"
THREADS="${LOAD_TEST_THREADS:-2}"

log_step "Load Test Configuration"
log_info "Target URL   : $TARGET_URL"
log_info "Duration     : ${DURATION}s"
log_info "Connections  : $CONNECTIONS"
log_info "Threads      : $THREADS"

if ! command -v wrk > /dev/null 2>&1; then
    log_error "wrk not found. Install with: sudo apt-get install wrk"
    exit 1
fi

# Kiem tra target co online khong truoc khi chay, that bai thi dung gon gang co thong bao ro
log_step "Pre-flight check: kiem tra target co dang online khong"
PREFLIGHT_OK=0
for attempt in 1 2 3; do
    if curl -sf --max-time 3 -o /dev/null "$TARGET_URL/health" 2>/dev/null \
       || curl -sf --max-time 3 -o /dev/null "$TARGET_URL/" 2>/dev/null; then
        PREFLIGHT_OK=1
        break
    fi
    log_warn "Lan thu $attempt/3: chua ket noi duoc toi $TARGET_URL, cho 3s..."
    sleep 3
done

if [ "$PREFLIGHT_OK" -ne 1 ]; then
    log_error "Khong ket noi duoc toi $TARGET_URL sau 3 lan thu."
    log_error "Kiem tra: da deploy len K8s chua? Da chay 'kubectl port-forward' hoac dung dia chi NodePort chua?"
    log_error "Load test chi dung duoc SAU KHI da deploy.sh xong va nginx dang chay."
    exit 1
fi
log_info "Target online, tiep tuc load test."

# Moi lan chay wrk khong lam sap toan bo script neu 1 phase loi giua duong
run_wrk() {
    local description="$1"
    shift
    log_info "Dang chay: $description"
    set +e
    "$@"
    local exit_code=$?
    set -e
    if [ $exit_code -ne 0 ]; then
        log_warn "$description ket thuc voi loi (exit code $exit_code) - bo qua, tiep tuc phase sau."
    fi
    return 0
}

log_step "Phase 1/3 Warm up (60s, low traffic)"
run_wrk "Warm up" wrk -t2 -c5 -d60s --latency "$TARGET_URL/"

log_step "Phase 2/3 Normal load (${DURATION}s)"
run_wrk "Normal load" wrk -t"$THREADS" -c"$CONNECTIONS" -d"${DURATION}s" --latency "$TARGET_URL/"

log_step "Phase 3/3 Mixed endpoints"
for ENDPOINT in / /health /api/test /login; do
    log_info "Testing endpoint: $ENDPOINT"
    run_wrk "Endpoint $ENDPOINT" wrk -t2 -c10 -d30s --latency "$TARGET_URL$ENDPOINT"
done

log_info "Load test completed"
log_info "Check Grafana dashboard for feature vectors and model activity"
