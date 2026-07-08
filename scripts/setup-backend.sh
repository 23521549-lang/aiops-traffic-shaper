#!/bin/bash
set -euo pipefail
export AWS_PAGER=""

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
REGION="ap-southeast-1"
PROJECT_NAME="aiops-traffic-shaper"
ENVIRONMENT="prod"
BUCKET_NAME="${PROJECT_NAME}-${ENVIRONMENT}-terraform-state"
TABLE_NAME="${PROJECT_NAME}-${ENVIRONMENT}-terraform-lock"

log_step "1/4 Verify AWS credentials"
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    log_error "AWS CLI not configured"
    exit 1
fi
log_info "AWS credentials verified"

log_step "2/4 Create S3 bucket for Terraform state"
if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
    log_info "S3 bucket already exists: $BUCKET_NAME"
else
    aws s3api create-bucket \
        --bucket "$BUCKET_NAME" \
        --region "$REGION" \
        --create-bucket-configuration LocationConstraint="$REGION"

    aws s3api put-bucket-versioning \
        --bucket "$BUCKET_NAME" \
        --versioning-configuration Status=Enabled

    aws s3api put-bucket-encryption \
        --bucket "$BUCKET_NAME" \
        --server-side-encryption-configuration \
        '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

    aws s3api put-public-access-block \
        --bucket "$BUCKET_NAME" \
        --public-access-block-configuration \
        "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

    log_info "S3 bucket created: $BUCKET_NAME"
fi

log_step "3/4 Create DynamoDB table for state locking"
if aws dynamodb describe-table --table-name "$TABLE_NAME" \
    --region "$REGION" > /dev/null 2>&1; then
    log_info "DynamoDB table already exists: $TABLE_NAME"
else
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions AttributeName=LockID,AttributeType=S \
        --key-schema AttributeName=LockID,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$REGION"

    aws dynamodb wait table-exists \
        --table-name "$TABLE_NAME" \
        --region "$REGION"

    log_info "DynamoDB table created: $TABLE_NAME"
fi

log_step "4/4 Initialize Terraform with S3 backend"
cd "$PROJECT_ROOT/terraform"
terraform init -reconfigure

cat << SUMMARY
------------------------------------------------------------------------
S3 Backend setup completed

S3 Bucket    : $BUCKET_NAME
DynamoDB     : $TABLE_NAME
Region       : $REGION

Terraform state is now stored remotely.
Multiple team members can safely run terraform commands.
------------------------------------------------------------------------
SUMMARY
