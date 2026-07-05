# Runbook: AI-Driven FinOps & Traffic Shaper
# Version: 2.0

## Initial Setup

### Prerequisites

- AWS CLI configured with IAM credentials
- Terraform >= 1.6.0
- kubectl installed
- Docker installed

### Step 1: Setup S3 Backend (one time only)

```bash
bash scripts/setup-backend.sh
```

### Step 2: Provision Infrastructure

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

### Step 3: Configure kubeconfig

```bash
$(terraform output -raw get_kubeconfig)
kubectl get nodes
```

Expected: 3 nodes in Ready state.

### Step 4: Build and Push Images

```bash
bash scripts/build-and-push.sh v1.0.0
```

### Step 5: Deploy Services

```bash
export INTERNAL_SECRET=$(openssl rand -hex 32)
export GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)
export AI_ENGINE_IMAGE=$(cd terraform && terraform output -json ecr_repository_urls   | python3 -c "import sys,json; print(json.load(sys.stdin)['ai-engine'])")":v1.0.0"
export WORKER_IMAGE=$(cd terraform && terraform output -json ecr_repository_urls   | python3 -c "import sys,json; print(json.load(sys.stdin)['worker-orchestrator'])")":v1.0.0"

bash scripts/deploy.sh v1.0.0
```

Deploy script automatically:
  Creates namespace and secrets
  Applies all K8s manifests in correct order
  Loads initial IP reputation into Redis
  Runs health check

### Step 6: Sync secrets to GitHub

```bash
bash scripts/sync-secrets.sh
```

## Day-2 Operations

### Check System Status

```bash
kubectl get pods -n aiops -o wide
kubectl top pods -n aiops
```

### View Logs

```bash
kubectl logs -f deployment/ai-engine -n aiops
kubectl logs -f deployment/worker-orchestrator -n aiops
kubectl logs -n aiops daemonset/fluent-bit
```

### Check Active Blocklist

```bash
kubectl exec -n aiops deployment/worker-orchestrator --   curl -s -H "X-Internal-Token: $INTERNAL_SECRET"   http://localhost:8050/api/v1/blocklist
```

### Add IP to Whitelist (false positive)

This also adds the IP to training exclusion list automatically.

```bash
kubectl exec -n aiops deployment/worker-orchestrator --   curl -s -X POST   -H "X-Internal-Token: $INTERNAL_SECRET"   -H "Content-Type: application/json"   -d '{"ip": "1.2.3.4", "reason": "CDN edge node"}'   http://localhost:8050/api/v1/whitelist
```

### Remove IP from Training Exclusion (re-include in training)

```bash
kubectl exec -n aiops deployment/ai-engine --   curl -s -X DELETE   -H "X-Internal-Token: $INTERNAL_SECRET"   http://localhost:8000/api/v1/whitelist/1.2.3.4/training-exclusion
```

### Manually Unblock an IP

```bash
kubectl exec -n aiops deployment/worker-orchestrator --   curl -s -X DELETE   -H "X-Internal-Token: $INTERNAL_SECRET"   http://localhost:8050/api/v1/blocklist/1.2.3.4
```

### Check Model Status

```bash
kubectl exec -n aiops deployment/ai-engine --   curl -s -H "X-Internal-Token: $INTERNAL_SECRET"   http://localhost:8000/api/v1/model/status
```

### Trigger Manual Retrain

```bash
kubectl exec -n aiops deployment/ai-engine --   curl -s -X POST   -H "X-Internal-Token: $INTERNAL_SECRET"   http://localhost:8000/api/v1/model/retrain
```

### Manually Update IP Reputation

```bash
kubectl create job --from=cronjob/ip-reputation-updater   ip-reputation-manual-$(date +%s) -n aiops
```

## Testing

### Load Test (generate normal traffic baseline)

```bash
bash scripts/load-test.sh http://<worker-node-ip>:30080
```

### Attack Simulation (test detection)

```bash
bash scripts/simulate-attack.sh http://<worker-node-ip>:30080 all
bash scripts/simulate-attack.sh http://<worker-node-ip>:30080 ddos
bash scripts/simulate-attack.sh http://<worker-node-ip>:30080 scanner
bash scripts/simulate-attack.sh http://<worker-node-ip>:30080 botnet
```

## Deployment

### Deploy New Version

```bash
bash scripts/build-and-push.sh v1.1.0
export AI_ENGINE_IMAGE=<ecr-url>/ai-engine:v1.1.0
export WORKER_IMAGE=<ecr-url>/worker-orchestrator:v1.1.0
bash scripts/deploy.sh v1.1.0
```

### Rollback

```bash
bash scripts/rollback.sh ai-engine
bash scripts/rollback.sh worker-orchestrator
```

## Troubleshooting

### Pod Stuck in Pending

```bash
kubectl describe pod <pod-name> -n aiops
```

Common causes: insufficient resources, PVC not bound, image pull error.

### AI Engine Not Receiving Logs

```bash
kubectl get pods -n aiops -l app=fluent-bit
kubectl logs -n aiops daemonset/fluent-bit
kubectl exec -n aiops deployment/nginx-proxy -c nginx --   tail -5 /var/log/nginx/access.log
```

### Circuit Breaker Open

Check Worker Orchestrator status:
```bash
kubectl logs -n aiops deployment/worker-orchestrator | grep -i "error\|circuit"
kubectl exec -n aiops deployment/ai-engine --   curl -s http://localhost:8000/health
```

### Model Not Training

Check shadow data volume:
```bash
kubectl exec -n aiops statefulset/redis --   redis-cli XLEN training:shadow_data
```

Need at least 100 records. If less, run load test first.

### Feature Drift Alert

```bash
kubectl exec -n aiops deployment/ai-engine --   curl -s -H "X-Internal-Token: $INTERNAL_SECRET"   http://localhost:8000/api/v1/model/status
```

If drift > 2.0 for any feature:
1. Check Grafana for ongoing attack
2. If legitimate traffic change, trigger manual retrain
3. Monitor score distribution for 1 hour after retrain

## Grafana Access

URL:      http://<worker-node-ip>:30300
Username: admin
Password: value of GRAFANA_ADMIN_PASSWORD

### Key Queries
Anomaly rate            rate(ai_anomalies_detected_total[5m])

Active blocks           nginx_blocked_ips_total

Active rate limits      nginx_rate_limited_ips_total

Cost saved (1h)         increase(estimated_cloud_cost_saved_usd[1h])

Shadow mode             shadow_mode_active

Feature drift           feature_drift_score

Score mean              model_anomaly_score_mean

Circuit breaker         look for gaps in nginx_blocked_ips_total

when ai_anomalies_detected_total is rising
