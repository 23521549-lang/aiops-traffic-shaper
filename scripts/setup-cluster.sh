#!/bin/bash
set -euo pipefail
export AWS_PAGER=""

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }
log_warn()  { echo "$LOG_PREFIX [WARN]  $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TERRAFORM_DIR="$PROJECT_ROOT/terraform"
TFVARS_FILE="$TERRAFORM_DIR/terraform.tfvars"

extract_tfvar() {
    local var_name="$1"
    local file="$2"
    grep -E "^[[:space:]]*${var_name}[[:space:]]*=" "$file" \
        | head -n1 \
        | sed -E 's/^[^=]+=[[:space:]]*"?([^"]*)"?[[:space:]]*$/\1/'
}

log_step "1/7 Verify prerequisites"
for cmd in terraform aws kubectl; do
    if ! command -v "$cmd" > /dev/null 2>&1; then
        log_error "Required command not found: $cmd"
        exit 1
    fi
done

if [ ! -f "$TFVARS_FILE" ]; then
    log_error "Khong tim thay $TFVARS_FILE"
    exit 1
fi

if ! aws sts get-caller-identity > /dev/null 2>&1; then
    log_error "Khong xac thuc duoc voi AWS trong session terminal hien tai."
    log_error "Kha nang cao ban chua export AWS_PROFILE trong terminal nay. Chay:"
    log_error "    export AWS_PROFILE=aiops-terraform"
    log_error "roi chay lai script nay."
    exit 1
fi
CALLER_ARN=$(aws sts get-caller-identity --query "Arn" --output text)
log_info "Dang dung credential: $CALLER_ARN"
log_info "All prerequisites verified"

log_step "2/7 Doc region va key_name tu terraform.tfvars"
REGION=$(extract_tfvar "region" "$TFVARS_FILE")
KEY_NAME=$(extract_tfvar "key_name" "$TFVARS_FILE")

if [ -z "$REGION" ] || [ -z "$KEY_NAME" ]; then
    log_error "Khong doc duoc 'region' hoac 'key_name' tu $TFVARS_FILE"
    log_error "Kiem tra file co dong 'region = \"...\"' va 'key_name = \"...\"' khong."
    exit 1
fi
log_info "region   = $REGION"
log_info "key_name = $KEY_NAME"

log_step "3/7 Kiem tra / tao EC2 key pair '$KEY_NAME'"
PEM_FILE="$HOME/.ssh/${KEY_NAME}.pem"

KEY_EXISTS_ON_AWS=0
if aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" > /dev/null 2>&1; then
    KEY_EXISTS_ON_AWS=1
fi

if [ "$KEY_EXISTS_ON_AWS" -eq 1 ]; then
    if [ -f "$PEM_FILE" ]; then
        log_info "Key pair '$KEY_NAME' da ton tai tren AWS va da co file private key tai $PEM_FILE. Bo qua."
    else
        log_error "Key pair '$KEY_NAME' DA TON TAI tren AWS, nhung KHONG tim thay file private key tai $PEM_FILE."
        log_error "AWS khong luu lai private key sau khi tao, nen KHONG THE lay lai file .pem cu."
        log_error "Chon 1 trong 2 cach:"
        log_error "  (a) Neu ban con giu file .pem o noi khac, copy vao dung duong dan: $PEM_FILE"
        log_error "  (b) Neu da mat han, xoa key pair cu tren AWS roi chay lai script nay de tao moi:"
        log_error "      aws ec2 delete-key-pair --key-name $KEY_NAME --region $REGION"
        exit 1
    fi
else
    log_info "Key pair '$KEY_NAME' chua ton tai tren AWS. Dang tao moi..."
    mkdir -p "$HOME/.ssh"
    aws ec2 create-key-pair \
        --key-name "$KEY_NAME" \
        --query "KeyMaterial" \
        --output text \
        --region "$REGION" > "$PEM_FILE"
    chmod 600 "$PEM_FILE"
    log_info "Da tao key pair moi, private key luu tai: $PEM_FILE"
fi

log_step "4/7 Terraform init"
cd "$TERRAFORM_DIR"
terraform init
log_info "Terraform init completed"

log_step "5/7 Terraform plan (xem truoc thay doi, CHUA ap dung gi ca)"
terraform plan -out=tfplan.out
echo ""
log_info "Doc ky plan phia tren TRUOC KHI xac nhan. Day la buoc cuoi de huy an toan."

if [ "${AUTO_APPROVE:-}" = "true" ]; then
    log_info "AUTO_APPROVE=true - bo qua xac nhan (dung cho CI/tu dong hoa)."
else
    echo ""
    read -r -p "Ban co chac muon APPLY plan tren len AWS THAT khong? (y/N): " CONFIRM_APPLY
    if [[ ! "$CONFIRM_APPLY" =~ ^[Yy]$ ]]; then
        log_info "Da huy theo yeu cau. KHONG co gi thay doi tren AWS."
        rm -f tfplan.out
        exit 0
    fi
fi

log_step "6/7 Terraform apply (dung dung file plan da duyet, khong plan lai lan 2)"
terraform apply tfplan.out
rm -f tfplan.out
log_info "Terraform apply completed"

log_step "7/8 Cho master init hoan tat va lay kubeconfig (co the mat vai phut)"
KUBECONFIG_CMD=$(terraform output -raw get_kubeconfig)
MAX_RETRY_KC=30
RETRY_KC=0
until eval "$KUBECONFIG_CMD" 2>/dev/null; do
    RETRY_KC=$((RETRY_KC + 1))
    if [ "$RETRY_KC" -ge "$MAX_RETRY_KC" ]; then
        log_error "Khong lay duoc kubeconfig sau $MAX_RETRY_KC lan thu (~$((MAX_RETRY_KC * 15))s)."
        log_error "Kiem tra thu cong bang cach SSH vao master va xem log:"
        MASTER_IP=$(terraform output -raw master_public_ip 2>/dev/null || echo "<master-ip>")
        log_error "    ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@$MASTER_IP 'tail -50 /var/log/master-init.log'"
        exit 1
    fi
    log_info "Master chua san sang (dang chay kubeadm init ben trong, binh thuong mat 2-5 phut), thu lai $RETRY_KC/$MAX_RETRY_KC sau 15s..."
    sleep 15
done
log_info "kubeconfig configured"

log_step "8/8 Verify cluster"
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
