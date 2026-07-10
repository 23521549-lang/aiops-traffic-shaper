#!/bin/bash
set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_warn()  { echo "$LOG_PREFIX [WARN]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

NAMESPACE="aiops"
MAX_RETRY=20
RETRY_INTERVAL=10

wait_for_deployment() {
    local name="$1"
    local retry=0

    until kubectl rollout status deployment/"$name" -n "$NAMESPACE" --timeout=10s > /dev/null 2>&1; do
        retry=$((retry + 1))
        if [ "$retry" -ge "$MAX_RETRY" ]; then
            log_error "Deployment $name not ready after $MAX_RETRY attempts"
            kubectl describe deployment "$name" -n "$NAMESPACE"
            return 1
        fi
        log_warn "$name not ready yet, attempt $retry/$MAX_RETRY, retrying in ${RETRY_INTERVAL}s"
        sleep "$RETRY_INTERVAL"
    done
    log_info "Deployment $name is ready"
}

wait_for_statefulset() {
    local name="$1"
    local retry=0

    until kubectl rollout status statefulset/"$name" -n "$NAMESPACE" --timeout=10s > /dev/null 2>&1; do
        retry=$((retry + 1))
        if [ "$retry" -ge "$MAX_RETRY" ]; then
            log_error "StatefulSet $name not ready after $MAX_RETRY attempts"
            kubectl describe statefulset "$name" -n "$NAMESPACE"
            return 1
        fi
        log_warn "$name not ready yet, attempt $retry/$MAX_RETRY, retrying in ${RETRY_INTERVAL}s"
        sleep "$RETRY_INTERVAL"
    done
    log_info "StatefulSet $name is ready"
}

log_step "1/4 Check all pods running"
wait_for_statefulset "redis"
wait_for_deployment "nginx-proxy"
wait_for_deployment "ai-engine"
wait_for_deployment "worker-orchestrator"
wait_for_deployment "prometheus"
wait_for_deployment "grafana"

log_step "2/4 Check pod status"
FAILED_PODS=$(kubectl get pods -n "$NAMESPACE"     --field-selector="status.phase!=Running,status.phase!=Succeeded"     --no-headers 2>/dev/null | wc -l)

if [ "$FAILED_PODS" -gt 0 ]; then
    log_warn "Some pods are not in Running state:"
    kubectl get pods -n "$NAMESPACE"         --field-selector="status.phase!=Running,status.phase!=Succeeded"
else
    log_info "All pods are running"
fi

log_step "3/4 Check service endpoints"
for svc in redis ai-engine worker-orchestrator nginx-proxy prometheus grafana; do
    ENDPOINTS=$(kubectl get endpoints "$svc" -n "$NAMESPACE"         --no-headers 2>/dev/null | awk '{print $2}')
    if [ "$ENDPOINTS" = "<none>" ] || [ -z "$ENDPOINTS" ]; then
        log_warn "Service $svc has no endpoints"
    else
        log_info "Service $svc has endpoints (details hidden from public log)"
    fi
done

log_step "4/4 Summary"
kubectl get pods -n "$NAMESPACE"

log_info "Health check completed"
