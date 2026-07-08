#!/bin/bash
set -euo pipefail
export AWS_PAGER=""

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }
log_warn()  { echo "$LOG_PREFIX [WARN]  $*"; }

IAM_USER="aiops-terraform"
DEFAULT_REGION="ap-southeast-1"
TEMP_PROFILE="bootstrap-root-temp"
POLICIES=(
    "arn:aws:iam::aws:policy/AmazonEC2FullAccess"
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryFullAccess"
    "arn:aws:iam::aws:policy/AmazonS3FullAccess"
    "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess"
    "arn:aws:iam::aws:policy/AmazonSSMFullAccess"
    "arn:aws:iam::aws:policy/AmazonVPCFullAccess"
    "arn:aws:iam::aws:policy/IAMFullAccess"
    "arn:aws:iam::aws:policy/ServiceQuotasFullAccess"
)

USED_TEMP_ROOT_PROFILE=0
BOOTSTRAP_PROFILE=""

log_step "0/6 Kiem tra AWS credentials hien tai"

EXISTING_ARN=""
if aws sts get-caller-identity > /dev/null 2>&1; then
    EXISTING_ARN=$(aws sts get-caller-identity --query "Arn" --output text 2>/dev/null || echo "")
fi

if [ -n "$EXISTING_ARN" ] && [[ "$EXISTING_ARN" != *":root" ]]; then
    log_info "Da co credential IAM hop le (khong phai root): $EXISTING_ARN"
    log_info "Dung luon credential nay, khong can nhap lai."
    BOOTSTRAP_PROFILE=""
else
    if [ -n "$EXISTING_ARN" ]; then
        log_warn "Credential hien tai la ROOT ($EXISTING_ARN)."
    else
        log_warn "Chua co AWS credential nao duoc cau hinh."
    fi
    log_info "Nhap Access Key cua ROOT user de bootstrap (chi dung 1 lan, se bi xoa o cuoi script)."
    echo ""
    read -r -p "AWS Access Key ID (root): " ROOT_ACCESS_KEY_ID
    read -r -s -p "AWS Secret Access Key (root, an khi nhap): " ROOT_SECRET_ACCESS_KEY
    echo ""
    read -r -p "AWS Region [${DEFAULT_REGION}]: " INPUT_REGION
    REGION="${INPUT_REGION:-$DEFAULT_REGION}"

    if [ -z "$ROOT_ACCESS_KEY_ID" ] || [ -z "$ROOT_SECRET_ACCESS_KEY" ]; then
        log_error "Access Key ID hoac Secret Access Key rong. Dung lai."
        exit 1
    fi

    aws configure set aws_access_key_id "$ROOT_ACCESS_KEY_ID" --profile "$TEMP_PROFILE"
    aws configure set aws_secret_access_key "$ROOT_SECRET_ACCESS_KEY" --profile "$TEMP_PROFILE"
    aws configure set region "$REGION" --profile "$TEMP_PROFILE"
    aws configure set output "json" --profile "$TEMP_PROFILE"

    log_info "Dang xac minh credential vua nhap..."
    if ! aws sts get-caller-identity --profile "$TEMP_PROFILE" > /dev/null 2>&1; then
        log_error "Khong xac thuc duoc voi AWS. Kiem tra lai Access Key/Secret."
        exit 1
    fi
    VERIFY_ARN=$(aws sts get-caller-identity --profile "$TEMP_PROFILE" --query "Arn" --output text)
    log_info "Xac thuc thanh cong: $VERIFY_ARN"

    BOOTSTRAP_PROFILE="$TEMP_PROFILE"
    USED_TEMP_ROOT_PROFILE=1
fi

PROFILE_ARGS=()
if [ -n "$BOOTSTRAP_PROFILE" ]; then
    PROFILE_ARGS=(--profile "$BOOTSTRAP_PROFILE")
fi

log_step "1/6 Xac minh AWS CLI va lay Account ID"
CALLER=$(aws sts get-caller-identity "${PROFILE_ARGS[@]}" --output json)
ACCOUNT_ID=$(echo "$CALLER" | python3 -c "import sys,json; print(json.load(sys.stdin)['Account'])")
log_info "AWS Account ID: $ACCOUNT_ID"
log_info "Caller: $(echo "$CALLER" | python3 -c "import sys,json; print(json.load(sys.stdin)['Arn'])")"

log_step "2/6 Tao IAM user: $IAM_USER"
if aws iam get-user --user-name "$IAM_USER" "${PROFILE_ARGS[@]}" > /dev/null 2>&1; then
    log_info "IAM user $IAM_USER da ton tai, bo qua tao moi"
else
    aws iam create-user --user-name "$IAM_USER" "${PROFILE_ARGS[@]}"
    log_info "Da tao IAM user $IAM_USER"
fi

log_step "3/6 Gan policy cho $IAM_USER"
for POLICY_ARN in "${POLICIES[@]}"; do
    aws iam attach-user-policy \
        --user-name "$IAM_USER" \
        --policy-arn "$POLICY_ARN" \
        "${PROFILE_ARGS[@]}"
    log_info "Attached: $POLICY_ARN"
done

log_step "4/6 Tao access key cho $IAM_USER"
EXISTING_KEYS=$(aws iam list-access-keys \
    --user-name "$IAM_USER" \
    --query "AccessKeyMetadata[].AccessKeyId" \
    --output text \
    "${PROFILE_ARGS[@]}")

if [ -n "$EXISTING_KEYS" ]; then
    log_info "Xoa access key cu cua $IAM_USER"
    for KEY_ID in $EXISTING_KEYS; do
        aws iam delete-access-key \
            --user-name "$IAM_USER" \
            --access-key-id "$KEY_ID" \
            "${PROFILE_ARGS[@]}"
        log_info "Da xoa key: $KEY_ID"
    done
fi

KEY_OUTPUT=$(aws iam create-access-key \
    --user-name "$IAM_USER" \
    "${PROFILE_ARGS[@]}" \
    --output json)

ACCESS_KEY_ID=$(echo "$KEY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKey']['AccessKeyId'])")
SECRET_ACCESS_KEY=$(echo "$KEY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKey']['SecretAccessKey'])")
log_info "Access key da tao: $ACCESS_KEY_ID"

log_step "5/6 Cau hinh AWS CLI profile cho $IAM_USER"
aws configure set aws_access_key_id "$ACCESS_KEY_ID" --profile "$IAM_USER"
aws configure set aws_secret_access_key "$SECRET_ACCESS_KEY" --profile "$IAM_USER"
aws configure set region "$DEFAULT_REGION" --profile "$IAM_USER"
aws configure set output "json" --profile "$IAM_USER"
log_info "Da cau hinh AWS CLI profile: $IAM_USER"

log_info "Doi vai giay de IAM propagation (quyen moi tao can vai giay de co hieu luc)..."
sleep 5

log_info "Kiem tra profile $IAM_USER hoat dong dung..."
if aws sts get-caller-identity --profile "$IAM_USER" > /dev/null 2>&1; then
    log_info "Profile $IAM_USER hoat dong tot."
else
    log_warn "Profile $IAM_USER chua the xac thuc ngay (co the do IAM can them thoi gian propagate). Thu lai sau vai giay."
fi

if [ "$USED_TEMP_ROOT_PROFILE" -eq 1 ]; then
    log_step "6/6 Don dep: xoa access key ROOT tam thoi"
    echo ""
    read -r -p "Da tao xong $IAM_USER thanh cong. Xoa ngay access key ROOT vua dung khong? (y/N): " CONFIRM_DELETE
    if [[ "$CONFIRM_DELETE" =~ ^[Yy]$ ]]; then
        aws iam delete-access-key \
            --access-key-id "$ROOT_ACCESS_KEY_ID" \
            --profile "$TEMP_PROFILE"
        log_info "Da xoa access key ROOT: $ROOT_ACCESS_KEY_ID"

        python3 - "$TEMP_PROFILE" << 'PYEOF'
import configparser, os, sys
profile = sys.argv[1]
for fname in [os.path.expanduser("~/.aws/credentials"), os.path.expanduser("~/.aws/config")]:
    if not os.path.exists(fname):
        continue
    cp = configparser.ConfigParser()
    cp.read(fname)
    section_names = [profile, f"profile {profile}"]
    changed = False
    for s in section_names:
        if cp.has_section(s):
            cp.remove_section(s)
            changed = True
    if changed:
        with open(fname, "w") as f:
            cp.write(f)
PYEOF
        log_info "Da xoa profile tam '$TEMP_PROFILE' khoi ~/.aws/credentials va ~/.aws/config"
    else
        log_warn "Ban chon KHONG xoa access key root. Access Key ID: $ROOT_ACCESS_KEY_ID"
        log_warn "Nho tu xoa thu cong trong AWS Console (Security credentials) sau khi xong viec."
    fi
else
    log_info "Khong dung root tam thoi lan nay, khong co gi can don dep."
fi

cat << SUMMARY

------------------------------------------------------------------------
Bootstrap completed successfully

Account ID  : $ACCOUNT_ID
IAM User    : $IAM_USER
Profile     : $IAM_USER
Region      : $DEFAULT_REGION

Next steps:
  export AWS_PROFILE=$IAM_USER
  cd terraform
  terraform init
  terraform plan
  terraform apply
------------------------------------------------------------------------
SUMMARY
