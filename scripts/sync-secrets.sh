#!/bin/bash
set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_warn()  { echo "$LOG_PREFIX [WARN]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"

# ── NHOM 3: Bien local-only, KHONG sync len GitHub Secrets ────────
EXCLUDED_VARS=(
    "AWS_PROFILE"
    "AWS_REGION"
    "REDIS_HOST"
    "REDIS_PORT"
    "REDIS_DB"
    "WORKER_ORCHESTRATOR_URL"
    "GITHUB_REPO"
    "NGINX_RATE_LIMIT_RPS"
    "NGINX_RATE_LIMIT_BURST"
    "NGINX_STRICT_RATE_LIMIT_RPS"
    "NGINX_STRICT_RATE_LIMIT_BURST"
)

log_step "1/4 Verify prerequisites"
if ! command -v gh > /dev/null 2>&1; then
    log_error "GitHub CLI (gh) not found"
    log_error "Install: https://cli.github.com"
    exit 1
fi

if ! gh auth status > /dev/null 2>&1; then
    log_error "Not authenticated with GitHub CLI"
    log_error "Run: gh auth login"
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    log_error ".env file not found at $ENV_FILE"
    log_error "Copy .env.example to .env and fill in values"
    exit 1
fi

log_step "2/4 Detect GitHub repository"
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)
if [ -z "$REPO" ]; then
    log_error "Could not detect GitHub repository"
    log_error "Run this script from inside the git repository"
    exit 1
fi
log_info "Repository: $REPO"

log_step "3/4 Read .env and sync to GitHub Secrets"
synced=0
skipped=0
excluded=0

while IFS= read -r line || [ -n "$line" ]; do
    # Bỏ qua comment và dòng trống
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue

    # Parse KEY=VALUE
    if [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]]; then
        KEY="${BASH_REMATCH[1]}"
        VALUE="${BASH_REMATCH[2]}"

        # ── Bien dang "_FILE": doc noi dung TU FILE, day len secret voi
        # ten da bo hau to "_FILE". Dung cho secret nhieu dong (vd SSH key)
        # ma khong the nhet an toan vao 1 dong trong .env.
        if [[ "$KEY" == *_FILE ]]; then
            REAL_KEY="${KEY%_FILE}"

            if [ -z "$VALUE" ]; then
                log_warn "Skipping empty file path: $KEY"
                skipped=$((skipped + 1))
                continue
            fi

            EXPANDED_PATH="${VALUE/#\~/$HOME}"

            if [ ! -f "$EXPANDED_PATH" ]; then
                log_error "File khong ton tai cho $KEY: $EXPANDED_PATH"
                log_error "Bo qua $REAL_KEY - kiem tra lai duong dan trong .env"
                skipped=$((skipped + 1))
                continue
            fi

            gh secret set "$REAL_KEY" --repo "$REPO" < "$EXPANDED_PATH"
            log_info "Synced (tu file): $REAL_KEY  <-  $EXPANDED_PATH"
            synced=$((synced + 1))
            continue
        fi

        # Bỏ qua giá trị rỗng
        if [ -z "$VALUE" ]; then
            log_warn "Skipping empty value: $KEY"
            skipped=$((skipped + 1))
            continue
        fi

        # Bỏ qua biến local-only
        is_excluded=false
        for excluded_var in "${EXCLUDED_VARS[@]}"; do
            if [ "$KEY" = "$excluded_var" ]; then
                is_excluded=true
                break
            fi
        done

        if [ "$is_excluded" = true ]; then
            log_info "Excluded (local only): $KEY"
            excluded=$((excluded + 1))
            continue
        fi

        # Sync lên GitHub Secrets
        echo "$VALUE" | gh secret set "$KEY" --repo "$REPO" 2>/dev/null
        log_info "Synced: $KEY"
        synced=$((synced + 1))
    fi
done < "$ENV_FILE"

log_step "4/4 Summary"
log_info "Synced   : $synced secrets"
log_info "Skipped  : $skipped (empty values / file not found)"
log_info "Excluded : $excluded (local-only vars)"

cat << SUMMARY
------------------------------------------------------------------------
GitHub Secrets sync completed

Repository : $REPO
Synced     : $synced secrets

To verify:
  gh secret list --repo $REPO

Note: Empty values were skipped.
      Fill them in .env then re-run this script.
------------------------------------------------------------------------
SUMMARY
