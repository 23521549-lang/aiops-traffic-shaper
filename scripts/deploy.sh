#!/bin/bash
set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
K8S_DIR="$PROJECT_ROOT/k8s"
IMAGE_TAG="${1:-latest}"
NAMESPACE="aiops"

log_step "1/10 Verify prerequisites"
for cmd in kubectl; do
    if ! command -v "$cmd" > /dev/null 2>&1; then
        log_error "Required command not found: $cmd"
        exit 1
    fi
done

if ! kubectl cluster-info > /dev/null 2>&1; then
    log_error "Cannot connect to Kubernetes cluster"
    exit 1
fi
log_info "Cluster connection verified"

if [ -z "${AI_ENGINE_IMAGE:-}" ] || [ -z "${WORKER_IMAGE:-}" ]; then
    log_error "Required environment variables not set: AI_ENGINE_IMAGE, WORKER_IMAGE"
    exit 1
fi
if [ -z "${INTERNAL_SECRET:-}" ] || [ -z "${GRAFANA_ADMIN_PASSWORD:-}" ]; then
    log_error "Required environment variables not set: INTERNAL_SECRET, GRAFANA_ADMIN_PASSWORD"
    log_error "(Can luon o moi lan deploy, khong chi lan dau - dung de thay the \${INTERNAL_SECRET} trong fluent-bit configmap)"
    exit 1
fi
log_info "Image tag      : $IMAGE_TAG"
log_info "AI Engine image: $AI_ENGINE_IMAGE"
log_info "Worker image   : $WORKER_IMAGE"

log_step "2/10 Apply namespace"
kubectl apply -f "$K8S_DIR/namespace.yaml"
log_info "Namespace applied"

log_step "3/10 Apply secrets"
if ! kubectl get secret aiops-internal-secret -n "$NAMESPACE" > /dev/null 2>&1; then
    kubectl create secret generic aiops-internal-secret \
        --namespace "$NAMESPACE" \
        --from-literal=INTERNAL_SECRET="$INTERNAL_SECRET" \
        --from-literal=GRAFANA_ADMIN_PASSWORD="$GRAFANA_ADMIN_PASSWORD"
    log_info "Secret aiops-internal-secret created"
else
    log_info "Secret already exists, skipping"
fi

log_step "4/10 Apply network policies"
kubectl apply -f "$K8S_DIR/network-policy.yaml"
log_info "Network policies applied"

log_step "5/10 Apply PVCs"
kubectl apply -f "$K8S_DIR/redis/pvc.yaml"
kubectl apply -f "$K8S_DIR/ai-engine/pvc.yaml"
kubectl apply -f "$K8S_DIR/monitoring/prometheus-pvc.yaml"
kubectl apply -f "$K8S_DIR/monitoring/grafana-pvc.yaml"
log_info "PVCs applied"

log_step "6/10 Apply Redis"
kubectl apply -f "$K8S_DIR/redis/"
kubectl rollout status statefulset/redis -n "$NAMESPACE" --timeout=120s
log_info "Redis ready"

log_step "7/10 Apply Nginx"
kubectl apply -f "$K8S_DIR/nginx/"
kubectl rollout status deployment/nginx-proxy -n "$NAMESPACE" --timeout=120s
log_info "Nginx ready"

log_step "8/10 Apply AI Engine and Worker Orchestrator"
if ! kubectl set image deployment/ai-engine ai-engine="$AI_ENGINE_IMAGE" -n "$NAMESPACE" 2>/dev/null; then
    log_info "Deployment ai-engine chua ton tai, apply lan dau (thay the \${AI_ENGINE_IMAGE})"
    sed "s|\${AI_ENGINE_IMAGE}|$AI_ENGINE_IMAGE|g" "$K8S_DIR/ai-engine/deployment.yaml" | kubectl apply -f -
    kubectl apply -f "$K8S_DIR/ai-engine/service.yaml"
fi

if ! kubectl set image deployment/worker-orchestrator worker-orchestrator="$WORKER_IMAGE" -n "$NAMESPACE" 2>/dev/null; then
    log_info "Deployment worker-orchestrator chua ton tai, apply lan dau (thay the \${WORKER_IMAGE})"
    sed "s|\${WORKER_IMAGE}|$WORKER_IMAGE|g" "$K8S_DIR/worker-orchestrator/deployment.yaml" | kubectl apply -f -
    kubectl apply -f "$K8S_DIR/worker-orchestrator/service.yaml"
fi

kubectl apply -f "$K8S_DIR/worker-orchestrator/rbac.yaml"
kubectl apply -f "$K8S_DIR/ai-engine/hpa.yaml"

kubectl rollout status deployment/ai-engine -n "$NAMESPACE" --timeout=120s
kubectl rollout status deployment/worker-orchestrator -n "$NAMESPACE" --timeout=120s
log_info "AI Engine and Worker Orchestrator ready"

log_step "9/10 Apply FluentBit, Monitoring and IP Reputation CronJob"
sed "s|\${INTERNAL_SECRET}|$INTERNAL_SECRET|g" "$K8S_DIR/fluent-bit/configmap.yaml" | kubectl apply -f -
kubectl apply -f "$K8S_DIR/fluent-bit/rbac.yaml"
kubectl apply -f "$K8S_DIR/monitoring/"
kubectl apply -f "$K8S_DIR/reputation/"
log_info "FluentBit, Monitoring and Reputation CronJob applied"

log_step "10/10 Load initial IP reputation into Redis"
REDIS_POD=$(kubectl get pod -n "$NAMESPACE" \
    -l app=redis \
    -o jsonpath="{.items[0].metadata.name}" 2>/dev/null || true)

if [ -n "$REDIS_POD" ]; then
    log_info "Loading IP reputation blocklist into Redis"
    kubectl exec -n "$NAMESPACE" "$REDIS_POD" -- sh -c \
        "apk add --no-cache curl wget 2>/dev/null; \
         wget -qO /tmp/firehol.txt \
           https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset || true; \
         grep -v '^#' /tmp/firehol.txt | grep -v '^\$' | \
           grep -E '^[0-9]' | awk '{print \$1}' | \
           while read ip; do redis-cli SADD reputation:blacklist \"\$ip\" > /dev/null; done; \
         redis-cli EXPIRE reputation:blacklist 172800 > /dev/null; \
         COUNT=\$(redis-cli SCARD reputation:blacklist); \
         echo \"Loaded \$COUNT IPs into reputation:blacklist\""
    log_info "IP reputation loaded"
else
    log_error "Redis pod not found — skipping reputation load"
fi

bash "$SCRIPT_DIR/health-check.sh"

cat << SUMMARY
------------------------------------------------------------------------
Deploy completed successfully

Image Tag  : $IMAGE_TAG
Namespace  : $NAMESPACE

Access:
  Nginx    : http://<worker-node-ip>:30080
  Grafana  : http://<worker-node-ip>:30300

Useful commands:
  kubectl get pods -n $NAMESPACE
  kubectl logs -f deployment/ai-engine -n $NAMESPACE
  bash scripts/simulate-attack.sh http://<worker-ip>:30080 all
------------------------------------------------------------------------
SUMMARY
