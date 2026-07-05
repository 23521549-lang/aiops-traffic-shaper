#!/bin/bash
set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

NAMESPACE="aiops"
DEPLOYMENT="${1:-}"

if [ -z "$DEPLOYMENT" ]; then
    log_error "Usage: bash scripts/rollback.sh <deployment-name>"
    log_error "Available deployments: ai-engine, worker-orchestrator, nginx-proxy"
    exit 1
fi

log_step "1/2 Rolling back deployment: $DEPLOYMENT"
kubectl rollout undo deployment/"$DEPLOYMENT" -n "$NAMESPACE"
log_info "Rollback triggered for $DEPLOYMENT"

log_step "2/2 Waiting for rollback to complete"
kubectl rollout status deployment/"$DEPLOYMENT" -n "$NAMESPACE" --timeout=120s
log_info "Rollback completed for $DEPLOYMENT"

kubectl get pods -n "$NAMESPACE" -l app="$DEPLOYMENT"
