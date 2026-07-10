# Runbook: AI-Driven FinOps & Traffic Shaper
# Version: 2.1

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

Expected: 4 nodes in Ready state (1 control-plane + 3 workers).

### Step 4: Build and Push Images

```bash
bash scripts/build-and-push.sh v1.0.0
```

### Step 5: Deploy Services

```bash
export INTERNAL_SECRET=$(openssl rand -hex 32)
export GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)
export AI_ENGINE_IMAGE=$(cd terraform && terraform output -json ecr_repository_urls \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['ai-engine'])")":v1.0.0"
export WORKER_IMAGE=$(cd terraform && terraform output -json ecr_repository_urls \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['worker-orchestrator'])")":v1.0.0"

bash scripts/deploy.sh v1.0.0
```

Deploy script automatically:
- Creates namespace and secrets
- Applies all K8s manifests in the correct order (FluentBit ConfigMap
  before Nginx, since nginx-proxy's fluent-bit sidecar depends on it)
- Loads initial IP reputation into Redis
- Runs health check

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

nginx-proxy should show 3/3 ready containers: nginx, reloader, fluent-bit.

### View Logs

```bash
kubectl logs -f deployment/ai-engine -n aiops
kubectl logs -f deployment/worker-orchestrator -n aiops
kubectl logs -n aiops deployment/nginx-proxy -c fluent-bit
```

### Check Active Blocklist

```bash
kubectl exec -n aiops deployment/worker-orchestrator -- \
  curl -s -H "X-Internal-Token: $INTERNAL_SECRET" \
  http://localhost:8050/api/v1/blocklist
```

### Add IP to Whitelist (false positive)

This also adds the IP to the training exclusion list automatically.

```bash
kubectl exec -n aiops deployment/worker-orchestrator -- \
  curl -s -X POST \
  -H "X-Internal-Token: $INTERNAL_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"ip": "1.2.3.4", "reason": "CDN edge node"}' \
  http://localhost:8050/api/v1/whitelist
```

### Remove IP from Training Exclusion (re-include in training)

```bash
kubectl exec -n aiops deployment/ai-engine -- \
  curl -s -X DELETE \
  -H "X-Internal-Token: $INTERNAL_SECRET" \
  http://localhost:8000/api/v1/whitelist/1.2.3.4/training-exclusion
```

### Manually Unblock an IP

```bash
kubectl exec -n aiops deployment/worker-orchestrator -- \
  curl -s -X DELETE \
  -H "X-Internal-Token: $INTERNAL_SECRET" \
  http://localhost:8050/api/v1/blocklist/1.2.3.4
```

### Check Model Status

```bash
kubectl exec -n aiops deployment/ai-engine -- \
  curl -s -H "X-Internal-Token: $INTERNAL_SECRET" \
  http://localhost:8000/api/v1/model/status
```

### Trigger Manual Retrain

```bash
kubectl exec -n aiops deployment/ai-engine -- \
  curl -s -X POST \
  -H "X-Internal-Token: $INTERNAL_SECRET" \
  http://localhost:8000/api/v1/model/retrain
```

### Manually Update IP Reputation

```bash
kubectl create job --from=cronjob/ip-reputation-updater \
  ip-reputation-manual-$(date +%s) -n aiops
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

### Nginx Pod Stuck in ContainerCreating (FailedMount)

```bash
kubectl describe pod -n aiops -l app=nginx-proxy | tail -20
```

If the event shows `configmap "fluent-bit-config" not found`, the FluentBit
ConfigMap was not applied before the Nginx deployment. deploy.sh applies it
in step 6.5, before step 7 (Apply Nginx) — if this ordering was changed,
restore it, since the fluent-bit sidecar in the nginx-proxy pod depends on
that ConfigMap existing first.

### AI Engine Not Receiving Logs

```bash
kubectl get pods -n aiops -l app=nginx-proxy
kubectl logs -n aiops deployment/nginx-proxy -c fluent-bit
kubectl exec -n aiops deployment/nginx-proxy -c nginx -- \
  tail -5 /var/log/nginx/access.log
```

If fluent-bit logs show `unknown configuration property`, check
config/fluent-bit/fluent-bit.conf and k8s/fluent-bit/configmap.yaml for
properties not supported by the installed Fluent Bit version's output
plugin (for example, `Batch_Size` is not a valid property for the `http`
output plugin).

### Worker Nodes Not Joining the Cluster

```bash
kubectl get nodes -o wide
```

If only the control-plane node appears, check the join command stored in
SSM against the current master's public IP:

```bash
aws ssm get-parameter \
  --name "/aiops-traffic-shaper/k8s/join-command" \
  --with-decryption \
  --query "Parameter.Value" --output text \
  --region ap-southeast-1
```

If the IP in the join command does not match the current master's public
IP, the worker likely read a stale value from a previous deployment before
the new master finished overwriting it. master-init.sh.tpl clears this SSM
parameter at the very start of master initialization to prevent this race
condition. To recover manually without waiting for a full re-provision,
SSH into the master, generate a fresh join command, and run it on each
worker:

```bash
ssh -i ~/.ssh/aiops-keypair.pem ubuntu@<MASTER_PUBLIC_IP> \
  "sudo kubeadm token create --print-join-command"
# then on each worker:
ssh -i ~/.ssh/aiops-keypair.pem ubuntu@<WORKER_PUBLIC_IP> "sudo <join-command>"
```

If a worker previously attempted a join and failed partway through, reset
it first:

```bash
ssh -i ~/.ssh/aiops-keypair.pem ubuntu@<WORKER_PUBLIC_IP> \
  "sudo kubeadm reset -f && sudo <join-command>"
```

### terraform destroy Fails with RepositoryNotEmptyException

ECR repositories block deletion while they still contain images.
terraform/modules/ecr/main.tf sets `force_delete = true` on the ECR
repository resource to avoid this. If destroy still fails, delete images
manually before retrying:

```bash
aws ecr batch-delete-image \
  --repository-name aiops-traffic-shaper/ai-engine \
  --region ap-southeast-1 \
  --image-ids "$(aws ecr list-images \
    --repository-name aiops-traffic-shaper/ai-engine \
    --region ap-southeast-1 --query 'imageIds[*]' --output json)"
```

### Circuit Breaker Open

Check Worker Orchestrator status:

```bash
kubectl logs -n aiops deployment/worker-orchestrator | grep -i "error\|circuit"
kubectl exec -n aiops deployment/ai-engine -- curl -s http://localhost:8000/health
```

### Model Not Training

Check shadow data volume:

```bash
kubectl exec -n aiops statefulset/redis -- redis-cli XLEN training:shadow_data
```

Need at least 100 records. If less, run a load test first.

### Feature Drift Alert

```bash
kubectl exec -n aiops deployment/ai-engine -- \
  curl -s -H "X-Internal-Token: $INTERNAL_SECRET" \
  http://localhost:8000/api/v1/model/status
```

If drift > 2.0 for any feature:
1. Check Grafana for ongoing attack
2. If legitimate traffic change, trigger manual retrain
3. Monitor score distribution for 1 hour after retrain

## Known Issues and Fixes

| Issue | Root Cause | Fix |
|---|---|---|
| Worker nodes fail to join with a connection timeout to a stale IP | Race condition: worker read the join command from SSM before the new master overwrote a value left by a previous deployment | Clear the SSM join-command parameter at the start of master-init.sh.tpl, before any other setup steps |
| terraform destroy fails with RepositoryNotEmptyException | ECR blocks repository deletion while images remain | Add force_delete = true to the aws_ecr_repository resource |
| CI/CD logs exposed internal pod IPs and NodePort mappings on a public repo | health-check.sh used `kubectl get pods -o wide` and printed per-service endpoint IPs | Removed -o wide from the summary output and replaced endpoint IP logging with a boolean has-endpoints check |
| FluentBit pods in CrashLoopBackOff | fluent-bit.conf set `Batch_Size` on the http output plugin, which is not a supported property in Fluent Bit 3.0.7 | Removed Batch_Size from both config/fluent-bit/fluent-bit.conf and k8s/fluent-bit/configmap.yaml |
| FluentBit received no log data even after the crash was fixed | FluentBit ran as a DaemonSet with its own emptyDir volume, which is isolated per pod; it could not see nginx-proxy's separate emptyDir even on the same node | Moved FluentBit into the nginx-proxy pod as a sidecar container sharing the same emptyDir volume; removed the DaemonSet |
| terraform plan failed with "vars map does not contain key RETRY_INTERVAL" | A single `$` was removed from `$${RETRY_INTERVAL}` while editing a log message, turning an escaped literal into a real Terraform template variable reference | Restored the double-dollar escape so Terraform's templatefile() treats it as a literal bash variable, not a Terraform variable |
| nginx-proxy pod stuck in ContainerCreating with FailedMount for configmap "fluent-bit-config" | deploy.sh applied the Nginx manifests (step 7) before the FluentBit ConfigMap (originally created in step 9), but the nginx-proxy pod's fluent-bit sidecar now depends on that ConfigMap at pod creation time | Moved the FluentBit ConfigMap apply into a new step 6.5, before the Nginx apply step |

## Grafana Access

- URL: `http://<worker-node-ip>:30300`
- Username: `admin`
- Password: value of `GRAFANA_ADMIN_PASSWORD`

### Key Queries

| Purpose | Query |
|---|---|
| Anomaly rate | rate(ai_anomalies_detected_total[5m]) |
| Active blocks | nginx_blocked_ips_total |
| Active rate limits | nginx_rate_limited_ips_total |
| Cost saved (1h) | increase(estimated_cloud_cost_saved_usd[1h]) |
| Shadow mode | shadow_mode_active |
| Feature drift | feature_drift_score |
| Score mean | model_anomaly_score_mean |
| Circuit breaker | look for gaps in nginx_blocked_ips_total when ai_anomalies_detected_total is rising |
