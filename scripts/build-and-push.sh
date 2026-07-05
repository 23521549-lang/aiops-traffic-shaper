#!/bin/bash
set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TERRAFORM_DIR="$PROJECT_ROOT/terraform"
IMAGE_TAG="${1:-latest}"

log_step "1/5 Verify prerequisites"
for cmd in docker aws terraform; do
    if ! command -v "$cmd" > /dev/null 2>&1; then
        log_error "Required command not found: $cmd"
        exit 1
    fi
done
log_info "All prerequisites verified"

log_step "2/5 Get ECR details from Terraform output"
cd "$TERRAFORM_DIR"

REGION=$(terraform output -raw region 2>/dev/null || echo "ap-southeast-1")
REGISTRY_ID=$(terraform output -raw ecr_registry_id)
AI_ENGINE_URL=$(terraform output -json ecr_repository_urls | python3 -c "import sys,json; print(json.load(sys.stdin)['ai-engine'])")
WORKER_URL=$(terraform output -json ecr_repository_urls | python3 -c "import sys,json; print(json.load(sys.stdin)['worker-orchestrator'])")

log_info "Region        : $REGION"
log_info "Registry ID   : $REGISTRY_ID"
log_info "AI Engine URL : $AI_ENGINE_URL"
log_info "Worker URL    : $WORKER_URL"

log_step "3/5 Authenticate Docker with ECR"
aws ecr get-login-password --region "$REGION"     | docker login         --username AWS         --password-stdin         "$REGISTRY_ID.dkr.ecr.$REGION.amazonaws.com"
log_info "Docker authenticated with ECR"

log_step "4/5 Build Docker images"
cd "$PROJECT_ROOT"

log_info "Building ai-engine:$IMAGE_TAG"
docker build     --tag "$AI_ENGINE_URL:$IMAGE_TAG"     --tag "$AI_ENGINE_URL:latest"     --file services/ai_engine/Dockerfile     .
log_info "ai-engine image built"

log_info "Building worker-orchestrator:$IMAGE_TAG"
docker build     --tag "$WORKER_URL:$IMAGE_TAG"     --tag "$WORKER_URL:latest"     --file services/worker_orchestrator/Dockerfile     .
log_info "worker-orchestrator image built"

log_step "5/5 Push images to ECR"
docker push "$AI_ENGINE_URL:$IMAGE_TAG"
docker push "$AI_ENGINE_URL:latest"
log_info "ai-engine pushed: $AI_ENGINE_URL:$IMAGE_TAG"

docker push "$WORKER_URL:$IMAGE_TAG"
docker push "$WORKER_URL:latest"
log_info "worker-orchestrator pushed: $WORKER_URL:$IMAGE_TAG"

cat << SUMMARY
------------------------------------------------------------------------
Build and push completed successfully

Image Tag : $IMAGE_TAG
AI Engine : $AI_ENGINE_URL:$IMAGE_TAG
Worker    : $WORKER_URL:$IMAGE_TAG

Next step:
  bash scripts/deploy.sh $IMAGE_TAG
------------------------------------------------------------------------
SUMMARY
