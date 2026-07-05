# AI-Driven FinOps & Traffic Shaper

An automated, closed-loop AIOps platform that detects and mitigates
cost-inflating network anomalies (DDoS, scrapers, botnets) in real time
using a 3-layer protection architecture with unsupervised ML at its core.
Deployed on a self-managed Kubernetes cluster with full CI/CD automation.

## 3-Layer Protection Architecture
Internet Traffic

|

Layer 1: Nginx Rate Limiting      <-- immediate, no ML, 0ms latency

|

Layer 2: IsolationForest ML       <-- behavioral detection, ~3s latency

|

Layer 3: IP Reputation Blocklist  <-- known bad actors, O(1) lookup

|

Mitigation: Tier 1 rate-limit or Tier 2 hard block (auto-expiry TTL)

End-to-end detection latency: under 3 seconds.

## Key Technical Highlights

### MLOps Pipeline

- Unsupervised anomaly detection using Isolation Forest on 7 configurable
  behavioral features per source IP (Feature Registry pattern)
- 24-hour shadow mode collects baseline traffic before enabling mitigation
- Daily automated retraining with 80/20 train/validation split
- Model validation before promotion: block rate threshold + std regression check
- Feedback loop: whitelisted IPs automatically excluded from training data
- Feature drift detection using standard deviation from training baseline
- Weekly model archival with version history

### Infrastructure

- Self-managed Kubernetes cluster via Terraform on AWS EC2
  (1 master + 2 workers, ap-southeast-1)
- Terraform remote state on S3 + DynamoDB locking
- GitHub Actions OIDC — no static AWS credentials stored
- Kubernetes RBAC scoped to minimum required permissions
- Nginx hot-reload via inotify sidecar — no Docker socket exposure
- Network Policies restrict inter-pod communication
- PersistentVolumeClaims for model, Prometheus, and Grafana storage
- AWS SSM Parameter Store for automated K8s cluster join

### Backend & Reliability

- FastAPI with full async architecture (anyio thread pool for ML inference)
- Circuit breaker on AI Engine → Worker Orchestrator HTTP calls
  (5 failure threshold, 30s recovery timeout)
- Pydantic v2 models for all request/response validation
- Redis Sorted Sets for O(log N) sliding window (TTL-based auto-cleanup)
- Redis Stream with MAXLEN for bounded shadow training data storage
- Two-tier progressive mitigation with automatic TTL-based unblock

### Security

- GitHub Actions OIDC (no long-lived AWS credentials)
- X-Internal-Token authentication on all internal service calls
- Kubernetes Network Policies (deny-by-default between services)
- Docker containers run as non-root user (UID 1000)
- Grafana admin password separate from internal service token
- FluentBit RBAC scoped to namespace level only
- Terraform state encrypted at rest in S3

## Feature Registry
Feature               Signal Detected           Enabled

request_rate          Volumetric DDoS, flood       true

error_ratio           Scanner, brute force         true

avg_bytes_sent        Scraping, exfiltration       true

avg_request_time      Slowloris, exhaustion        true

unique_uri_ratio      Path scanner, crawler        true

user_agent_entropy    Botnet rotating UA           true

post_ratio            Credential stuffing          true

Add new features by adding to FEATURE_REGISTRY in feature_config.py.
No other code changes required.

## Prometheus Metrics

| Metric | Type | Description |
|---|---|---|
| ai_anomalies_detected_total | Counter | By reason and tier |
| nginx_blocked_ips_total | Gauge | Active hard-block count |
| nginx_rate_limited_ips_total | Gauge | Active rate-limit count |
| estimated_cloud_cost_saved_usd | Counter | Cost savings |
| shadow_mode_active | Gauge | 1=shadow, 0=live |
| feature_drift_score | Gauge | Per-feature drift |
| model_anomaly_score_mean | Gauge | Rolling score mean |
| inference_duration_seconds | Histogram | ML latency |

## Project Structure
aiops-traffic-shaper/

terraform/              IaC: VPC, EC2, ECR, S3 backend, OIDC

k8s/                    Kubernetes manifests (25+ files)

services/

ai-engine/          FastAPI + Isolation Forest + Feature Registry

worker-orchestrator/ Mitigation + ConfigMap patcher

config/                 Nginx (rate limiting) + FluentBit

scripts/                9 automation scripts

tests/                  57 unit test cases

docs/                   Architecture + MLOps design + Runbook

.github/workflows/      CI/CD with OIDC auth

## Quick Start

```bash
# 1. Clone and setup
git clone <repo-url>
cd aiops-traffic-shaper
cp .env.example .env
# Fill in .env values

# 2. Setup AWS backend (one time)
bash scripts/setup-backend.sh

# 3. Provision infrastructure
cd terraform && terraform apply

# 4. Build and push images
bash scripts/build-and-push.sh v1.0.0

# 5. Deploy
export INTERNAL_SECRET=$(openssl rand -hex 32)
export GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)
bash scripts/deploy.sh v1.0.0

# 6. Test detection
bash scripts/load-test.sh http://<worker-ip>:30080
bash scripts/simulate-attack.sh http://<worker-ip>:30080 all
```

## Documentation

- [Architecture Design](docs/architecture.md)
- [MLOps Design](docs/mlops-design.md)
- [Operations Runbook](docs/runbook.md)

## Technology Stack

| Layer | Technology |
|---|---|
| Proxy | Nginx 1.25 (rate limiting built-in) |
| Log Forwarding | FluentBit 3.0 |
| ML Framework | scikit-learn (Isolation Forest) |
| API Framework | FastAPI + Pydantic v2 |
| State Store | Redis 7 |
| Orchestration | Kubernetes 1.29 (kubeadm) |
| Infrastructure | Terraform + AWS EC2 + ECR + S3 |
| Observability | Prometheus + Grafana (7 alerts) |
| CI/CD | GitHub Actions + OIDC |
