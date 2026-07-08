#!/bin/bash
set -euo pipefail
export AWS_PAGER=""

log_info()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO]  $*"; }
log_error() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2; }
log_step()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [STEP]  -------- $* --------"; }
log_warn()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN]  $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TERRAFORM_DIR="$PROJECT_ROOT/terraform"
ENV_FILE="$PROJECT_ROOT/.env"
ENV_EXAMPLE="$PROJECT_ROOT/.env.example"

log_step "1/4 Verify prerequisites"
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    log_error "Khong xac thuc duoc voi AWS. Chay: export AWS_PROFILE=aiops-terraform"
    exit 1
fi
if [ ! -f "$ENV_EXAMPLE" ]; then
    log_error "Khong tim thay $ENV_EXAMPLE"
    exit 1
fi

if [ -f "$ENV_FILE" ]; then
    log_warn "Da ton tai $ENV_FILE"
    read -r -p "Ghi de len .env hien tai khong? (y/N): " CONFIRM_OVERWRITE
    if [[ ! "$CONFIRM_OVERWRITE" =~ ^[Yy]$ ]]; then
        log_info "Da huy. .env giu nguyen khong doi."
        exit 0
    fi
fi
cp "$ENV_EXAMPLE" "$ENV_FILE"
log_info "Da copy .env.example -> .env"

log_step "2/4 Lay gia tri tu Terraform output (Nhom 2)"
cd "$TERRAFORM_DIR"

MASTER_IP=$(terraform output -raw master_public_ip 2>/dev/null || echo "")
ROLE_ARN=$(terraform output -raw github_actions_role_arn 2>/dev/null || echo "")

if [ -z "$MASTER_IP" ] || [ -z "$ROLE_ARN" ]; then
    log_warn "Khong lay duoc terraform output (co the chua chay 'terraform apply')."
    log_warn "Bo qua MASTER_NODE_IP / AWS_GITHUB_ACTIONS_ROLE_ARN - dien tay sau."
else
    log_info "master_public_ip        = $MASTER_IP"
    log_info "github_actions_role_arn = $ROLE_ARN"
fi
cd "$PROJECT_ROOT"

log_step "3/4 Tu sinh secret ngau nhien (Nhom 4) va lay AWS Account ID"
INTERNAL_SECRET_VALUE=$(openssl rand -hex 32)
GRAFANA_PASSWORD_VALUE=$(openssl rand -hex 16)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
log_info "Da sinh INTERNAL_SECRET va GRAFANA_ADMIN_PASSWORD moi"
log_info "AWS Account ID: $ACCOUNT_ID"

log_step "4/4 Dien gia tri vao .env"
python3 - "$ENV_FILE" "$MASTER_IP" "$ROLE_ARN" "$ACCOUNT_ID" "$INTERNAL_SECRET_VALUE" "$GRAFANA_PASSWORD_VALUE" << 'PYEOF'
import sys, re

env_file, master_ip, role_arn, account_id, internal_secret, grafana_pw = sys.argv[1:7]

with open(env_file) as f:
    content = f.read()

replacements = {
    "INTERNAL_SECRET": internal_secret,
    "GRAFANA_ADMIN_PASSWORD": grafana_pw,
    "AWS_ACCOUNT_ID": account_id,
}
if master_ip:
    replacements["MASTER_NODE_IP"] = master_ip
if role_arn:
    replacements["AWS_GITHUB_ACTIONS_ROLE_ARN"] = role_arn

for key, value in replacements.items():
    pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)
    if pattern.search(content):
        content = pattern.sub(f"{key}={value}", content)

with open(env_file, "w") as f:
    f.write(content)

print("Da dien xong cac bien vao .env")
PYEOF

cat << SUMMARY

------------------------------------------------------------------------
.env da duoc tao/cap nhat tai: $ENV_FILE

Da tu dong dien:
  INTERNAL_SECRET          (tu sinh moi)
  GRAFANA_ADMIN_PASSWORD   (tu sinh moi)
  AWS_ACCOUNT_ID           = $ACCOUNT_ID
$([ -n "$MASTER_IP" ] && echo "  MASTER_NODE_IP           = $MASTER_IP")
$([ -n "$ROLE_ARN" ] && echo "  AWS_GITHUB_ACTIONS_ROLE_ARN = $ROLE_ARN")

CAN KIEM TRA / DIEN TAY THEM:
  MASTER_SSH_PRIVATE_KEY_FILE  (mac dinh: ~/.ssh/aiops-keypair.pem, doi neu khac)
  GITHUB_REPO                  (dien dung ten repo cua ban)
  allowed_ssh_cidr trong terraform.tfvars (khong lien quan file nay)

Buoc tiep theo:
  bash scripts/sync-secrets.sh
  gh secret list
------------------------------------------------------------------------
SUMMARY
