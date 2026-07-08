#!/bin/bash
set -uo pipefail
export AWS_PAGER=""

log_info()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO]  $*"; }
log_error() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2; }
log_step()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [STEP]  -------- $* --------"; }
log_warn()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN]  $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TERRAFORM_DIR="$PROJECT_ROOT/terraform"
TFVARS_FILE="$TERRAFORM_DIR/terraform.tfvars"

extract_tfvar() {
    grep -E "^[[:space:]]*${1}[[:space:]]*=" "$2" \
        | head -n1 \
        | sed -E 's/^[^=]+=[[:space:]]*"?([^"]*)"?[[:space:]]*$/\1/'
}

log_step "1/3 Verify AWS credentials"
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    log_error "Khong xac thuc duoc voi AWS. Chay: export AWS_PROFILE=aiops-terraform"
    exit 1
fi
log_info "Dang dung credential: $(aws sts get-caller-identity --query Arn --output text)"

REGION=$(extract_tfvar "region" "$TFVARS_FILE")
if [ -z "$REGION" ]; then
    log_error "Khong doc duoc 'region' tu $TFVARS_FILE"
    exit 1
fi
log_info "Region: $REGION"

log_step "2/3 Terraform destroy (co xac nhan truoc khi xoa)"
cd "$TERRAFORM_DIR"

if [ ! -f "terraform.tfstate" ] && ! terraform state list > /dev/null 2>&1; then
    log_warn "Khong tim thay Terraform state nao (co the da destroy tu truoc). Bo qua buoc destroy, chuyen sang kiem tra."
else
    terraform plan -destroy -out=destroy.tfplan
    echo ""
    log_info "Doc ky danh sach se bi XOA phia tren TRUOC KHI xac nhan."

    if [ "${AUTO_APPROVE:-}" = "true" ]; then
        log_info "AUTO_APPROVE=true - bo qua xac nhan."
    else
        echo ""
        read -r -p "Ban co chac muon XOA toan bo tai nguyen Terraform tren AWS THAT khong? (y/N): " CONFIRM_DESTROY
        if [[ ! "$CONFIRM_DESTROY" =~ ^[Yy]$ ]]; then
            log_info "Da huy theo yeu cau. KHONG co gi bi xoa."
            rm -f destroy.tfplan
            exit 0
        fi
    fi

    terraform apply destroy.tfplan
    rm -f destroy.tfplan
    log_info "Terraform destroy hoan tat."
fi

log_step "3/3 Quet toan bo tai nguyen CO THE TON TIEN tren AWS (khong chi nhung gi Terraform quan ly)"
echo ""

FOUND_BILLABLE=0

check_resource() {
    local label="$1"
    local result="$2"
    if [ -n "$result" ] && [ "$result" != "None" ]; then
        log_warn "CON SOT: $label"
        echo "$result" | sed 's/^/    /'
        FOUND_BILLABLE=1
    else
        log_info "OK - khong con: $label"
    fi
}

R=$(aws ec2 describe-instances --region "$REGION" \
    --query "Reservations[].Instances[?State.Name!='terminated'].[InstanceId,InstanceType,State.Name]" \
    --output text 2>/dev/null)
check_resource "EC2 instance (chua terminated)" "$R"

R=$(aws ec2 describe-volumes --region "$REGION" \
    --filters "Name=status,Values=available" \
    --query "Volumes[].[VolumeId,Size,VolumeType]" \
    --output text 2>/dev/null)
check_resource "EBS volume mo coi (khong gan instance, van tinh tien)" "$R"

R=$(aws ec2 describe-addresses --region "$REGION" \
    --query "Addresses[?AssociationId==null].[PublicIp,AllocationId]" \
    --output text 2>/dev/null)
check_resource "Elastic IP chua gan instance (tinh tien theo gio)" "$R"

R=$(aws ec2 describe-nat-gateways --region "$REGION" \
    --filter "Name=state,Values=available,pending" \
    --query "NatGateways[].[NatGatewayId,State]" \
    --output text 2>/dev/null)
check_resource "NAT Gateway (dat, tinh theo gio)" "$R"

R=$(aws elbv2 describe-load-balancers --region "$REGION" \
    --query "LoadBalancers[].[LoadBalancerArn,Type]" \
    --output text 2>/dev/null)
check_resource "Load Balancer ALB/NLB (tinh theo gio)" "$R"

R=$(aws elb describe-load-balancers --region "$REGION" \
    --query "LoadBalancerDescriptions[].LoadBalancerName" \
    --output text 2>/dev/null)
check_resource "Load Balancer Classic (tinh theo gio)" "$R"

R=$(aws rds describe-db-instances --region "$REGION" \
    --query "DBInstances[].[DBInstanceIdentifier,DBInstanceStatus]" \
    --output text 2>/dev/null)
check_resource "RDS instance (tinh theo gio)" "$R"

echo ""
log_step "Cac tai nguyen KHONG tinh tien dang ke - KHONG can xoa"
echo "    - S3 bucket terraform state (gan nhu 0 USD, chi vai KB du lieu)"
echo "    - DynamoDB table (PAY_PER_REQUEST, khong request thi khong tinh tien)"
echo "    - EC2 Key Pair (mien phi hoan toan)"
echo "    - VPC / Subnet / Route Table / Internet Gateway / Security Group (mien phi)"
echo "    - IAM Role / Policy (mien phi)"
echo "    - ECR repository rong hoac it image (phi luu tru rat nho, ~0.1 USD/GB/thang)"

echo ""
log_step "Xac nhan 3 tai nguyen KHONG bao gio bi script nay xoa (van con nguyen)"

KEY_NAME=$(extract_tfvar "key_name" "$TFVARS_FILE")
PROJECT_NAME="aiops-traffic-shaper"
ENVIRONMENT="prod"
BUCKET_NAME="${PROJECT_NAME}-${ENVIRONMENT}-terraform-state"
TABLE_NAME="${PROJECT_NAME}-${ENVIRONMENT}-terraform-lock"

confirm_survives() {
    local label="$1"
    local exists="$2"
    if [ "$exists" -eq 0 ]; then
        log_info "OK - $label van con nguyen (khong bi script nay dung toi)"
    else
        log_warn "KHONG TIM THAY: $label (co the da bi xoa TU TRUOC, khong phai do script nay)"
    fi
}

if [ -n "$KEY_NAME" ]; then
    aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" > /dev/null 2>&1
    confirm_survives "EC2 Key Pair '$KEY_NAME'" "$?"
fi

aws iam get-user --user-name "aiops-terraform" > /dev/null 2>&1
confirm_survives "IAM user 'aiops-terraform'" "$?"

aws s3api head-bucket --bucket "$BUCKET_NAME" > /dev/null 2>&1
confirm_survives "S3 bucket '$BUCKET_NAME'" "$?"

aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$REGION" > /dev/null 2>&1
confirm_survives "DynamoDB table '$TABLE_NAME'" "$?"

echo ""
if [ "$FOUND_BILLABLE" -eq 1 ]; then
    log_warn "=========================================================="
    log_warn "CO TAI NGUYEN CON SOT LAI CO THE DANG TINH TIEN - xem chi tiet phia tren"
    log_warn "=========================================================="
    exit 1
else
    log_info "=========================================================="
    log_info "SACH - KHONG con tai nguyen nao co the tinh tien dang ke"
    log_info "=========================================================="
    exit 0
fi
