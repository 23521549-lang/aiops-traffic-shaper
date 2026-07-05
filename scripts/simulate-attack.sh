#!/bin/bash
set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

TARGET_URL="${1:-http://localhost:30080}"
ATTACK_TYPE="${2:-all}"

if ! command -v curl > /dev/null 2>&1; then
    log_error "curl not found"
    exit 1
fi

simulate_ddos() {
    log_step "Simulating DDoS (high request rate from single IP)"
    log_info "Sending 200 rapid requests to $TARGET_URL"

    for i in $(seq 1 200); do
        curl -s -o /dev/null             -H "X-Forwarded-For: 10.0.0.1"             "$TARGET_URL/" &
    done
    wait
    log_info "DDoS simulation complete — check Grafana for request_rate spike"
}

simulate_scanner() {
    log_step "Simulating Web Scanner (path enumeration)"
    PATHS=(
        "/admin" "/wp-admin" "/wp-login.php" "/.env"
        "/config" "/backup" "/db" "/phpmyadmin"
        "/api/v1/users" "/api/v1/admin" "/api/debug"
        "/.git/config" "/server-status" "/actuator"
        "/swagger" "/graphql" "/api-docs"
    )

    for path in "${PATHS[@]}"; do
        curl -s -o /dev/null             -H "X-Forwarded-For: 10.0.0.2"             "$TARGET_URL$path"
        sleep 0.1
    done
    log_info "Scanner simulation complete — check for high error_ratio + unique_uri_ratio"
}

simulate_credential_stuffing() {
    log_step "Simulating Credential Stuffing (POST flood to login)"
    log_info "Sending 50 POST requests to /login"

    for i in $(seq 1 50); do
        curl -s -o /dev/null             -X POST             -H "X-Forwarded-For: 10.0.0.3"             -H "Content-Type: application/json"             -d "{"username":"user$i","password":"pass$i"}"             "$TARGET_URL/login"
        sleep 0.05
    done
    log_info "Credential stuffing simulation complete — check for high post_ratio"
}

simulate_botnet() {
    log_step "Simulating Botnet (rotating User-Agents)"
    USER_AGENTS=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605"
        "Mozilla/5.0 (X11; Linux x86_64) Firefox/121.0"
        "curl/7.88.1"
        "python-requests/2.31.0"
        "Go-http-client/1.1"
        "Apache-HttpClient/4.5.14"
        "okhttp/4.12.0"
    )

    for i in $(seq 1 40); do
        UA_INDEX=$((i % ${#USER_AGENTS[@]}))
        UA="${USER_AGENTS[$UA_INDEX]}"
        curl -s -o /dev/null             -H "X-Forwarded-For: 10.0.0.4"             -A "$UA"             "$TARGET_URL/"
        sleep 0.1
    done
    log_info "Botnet simulation complete — check for high user_agent_entropy"
}

simulate_slowloris() {
    log_step "Simulating Slowloris (slow connections)"
    log_info "Opening slow connections to $TARGET_URL"

    for i in $(seq 1 10); do
        curl -s -o /dev/null             -H "X-Forwarded-For: 10.0.0.5"             --limit-rate 10             --max-time 30             "$TARGET_URL/" &
    done
    sleep 15
    kill %% 2>/dev/null || true
    log_info "Slowloris simulation complete — check for high avg_request_time"
}

case "$ATTACK_TYPE" in
    ddos)
        simulate_ddos
        ;;
    scanner)
        simulate_scanner
        ;;
    credential_stuffing)
        simulate_credential_stuffing
        ;;
    botnet)
        simulate_botnet
        ;;
    slowloris)
        simulate_slowloris
        ;;
    all)
        log_step "Running all attack simulations"
        log_info "Each simulation targets a different source IP"
        simulate_ddos
        sleep 5
        simulate_scanner
        sleep 5
        simulate_credential_stuffing
        sleep 5
        simulate_botnet
        sleep 5
        simulate_slowloris
        ;;
    *)
        log_error "Unknown attack type: $ATTACK_TYPE"
        log_error "Valid types: ddos, scanner, credential_stuffing, botnet, slowloris, all"
        exit 1
        ;;
esac

cat << SUMMARY
------------------------------------------------------------------------
Attack simulation completed

Type     : $ATTACK_TYPE
Target   : $TARGET_URL

Expected detections in Grafana:
  ddos               → request_rate spike
  scanner            → error_ratio + unique_uri_ratio spike
  credential_stuffing → post_ratio + error_ratio spike
  botnet             → user_agent_entropy spike
  slowloris          → avg_request_time spike

Check:
  kubectl logs -f deployment/ai-engine -n aiops
  Grafana: http://<worker-ip>:30300
------------------------------------------------------------------------
SUMMARY
