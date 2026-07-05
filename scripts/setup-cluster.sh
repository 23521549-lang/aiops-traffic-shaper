#!/bin/bash
set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TERRAFORM_DIR="$PROJECT_ROOT/terraform"

log_step "1/4 Verify prerequisites"
for cmd in terraform aws kubectl; do
    if ! command -v "$cmd" > /dev/null 2>&1; then
        log_error "Required command not found: $cmd"
        exit 1
    fi
done
log_info "All prerequisites verified"

log_step "2/4 Terraform init and apply"
cd "$TERRAFORM_DIR"
terraform init
terraform apply -auto-approve
log_info "Terraform apply completed"

log_step "3/4 Configure kubeconfig"
KUBECONFIG_CMD=$(terraform output -raw get_kubeconfig)
eval "$KUBECONFIG_CMD"
log_info "kubeconfig configured"

log_step "4/4 Verify cluster"
MAX_RETRY=30
RETRY=0

until kubectl get nodes > /dev/null 2>&1; do
    RETRY=$((RETRY + 1))
    if [ "$RETRY" -ge "$MAX_RETRY" ]; then
        log_error "Cluster not ready after $MAX_RETRY attempts"
        exit 1
    fi
    log_info "Waiting for cluster to be ready, attempt $RETRY/$MAX_RETRY"
    sleep 10
done

NODE_COUNT=$(kubectl get nodes --no-headers | wc -l)
log_info "Cluster ready with $NODE_COUNT node(s)"
kubectl get nodes

cat << SUMMARY
------------------------------------------------------------------------
Cluster setup completed successfully

Next steps:
  1. Build and push Docker images:
     bash scripts/build-and-push.sh v1.0.0

  2. Deploy all services:
     export INTERNAL_SECRET=<your-secret>
     export AI_ENGINE_IMAGE=<ecr-url>/ai-engine:v1.0.0
     export WORKER_IMAGE=<ecr-url>/worker-orchestrator:v1.0.0
     bash scripts/deploy.sh v1.0.0
------------------------------------------------------------------------
SUMMARY
