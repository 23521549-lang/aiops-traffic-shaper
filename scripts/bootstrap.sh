#!/bin/bash
set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

IAM_USER="aiops-terraform"
POLICIES=(
    "arn:aws:iam::aws:policy/AmazonEC2FullAccess"
    "arn:aws:iam::aws:policy/AmazonECRFullAccess"
    "arn:aws:iam::aws:policy/AmazonSSMFullAccess"
    "arn:aws:iam::aws:policy/AmazonVPCFullAccess"
    "arn:aws:iam::aws:policy/IAMFullAccess"
)

log_step "1/5 Verify AWS CLI and credentials"
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    log_error "AWS CLI not configured. Run: aws configure"
    exit 1
fi

CALLER=$(aws sts get-caller-identity --output json)
ACCOUNT_ID=$(echo "$CALLER" | python3 -c "import sys,json; print(json.load(sys.stdin)['Account'])")
log_info "AWS Account ID: $ACCOUNT_ID"
log_info "Caller: $(echo "$CALLER" | python3 -c "import sys,json; print(json.load(sys.stdin)['Arn'])")"

log_step "2/5 Create IAM user: $IAM_USER"
if aws iam get-user --user-name "$IAM_USER" > /dev/null 2>&1; then
    log_info "IAM user $IAM_USER already exists, skipping creation"
else
    aws iam create-user --user-name "$IAM_USER"
    log_info "IAM user $IAM_USER created"
fi

log_step "3/5 Attach policies to $IAM_USER"
for POLICY_ARN in "${POLICIES[@]}"; do
    aws iam attach-user-policy         --user-name "$IAM_USER"         --policy-arn "$POLICY_ARN"
    log_info "Attached: $POLICY_ARN"
done

log_step "4/5 Create access key for $IAM_USER"
EXISTING_KEYS=$(aws iam list-access-keys     --user-name "$IAM_USER"     --query "AccessKeyMetadata[].AccessKeyId"     --output text)

if [ -n "$EXISTING_KEYS" ]; then
    log_info "Deleting existing access keys for $IAM_USER"
    for KEY_ID in $EXISTING_KEYS; do
        aws iam delete-access-key             --user-name "$IAM_USER"             --access-key-id "$KEY_ID"
        log_info "Deleted key: $KEY_ID"
    done
fi

KEY_OUTPUT=$(aws iam create-access-key     --user-name "$IAM_USER"     --output json)

ACCESS_KEY_ID=$(echo "$KEY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKey']['AccessKeyId'])")
SECRET_ACCESS_KEY=$(echo "$KEY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKey']['SecretAccessKey'])")
log_info "Access key created: $ACCESS_KEY_ID"

log_step "5/5 Configure AWS CLI profile for $IAM_USER"
aws configure set aws_access_key_id "$ACCESS_KEY_ID" --profile "$IAM_USER"
aws configure set aws_secret_access_key "$SECRET_ACCESS_KEY" --profile "$IAM_USER"
aws configure set region "ap-southeast-1" --profile "$IAM_USER"
aws configure set output "json" --profile "$IAM_USER"
log_info "AWS CLI profile configured: $IAM_USER"

cat << SUMMARY
------------------------------------------------------------------------
Bootstrap completed successfully

Account ID  : $ACCOUNT_ID
IAM User    : $IAM_USER
Profile     : $IAM_USER
Region      : ap-southeast-1

Next steps:
  export AWS_PROFILE=$IAM_USER
  cd terraform
  terraform init
  terraform plan
  terraform apply
------------------------------------------------------------------------
SUMMARY
