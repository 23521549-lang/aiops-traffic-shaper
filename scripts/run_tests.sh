#!/bin/bash
# Local test run for the hybrid model (ADR-002): services/backend + services/agent.
#
# Rewritten 2026-08-24 (Phase 6). The previous version tested the superseded
# ai_engine / worker_orchestrator services against a live Redis; those services
# were removed with the rest of the old model.
#
# Runs exactly what .github/workflows/ci.yml runs, so "green locally" and
# "green in CI" mean the same thing. Builds the venv from requirements.txt and
# nothing else — Phase 5 found requirements.txt was missing httpx2 precisely
# because no clean install had ever been attempted.
set -e

VENV="${VENV:-$HOME/aiops-venv-clean}"
cd "$(dirname "$0")/.."

log() { echo "[$(date '+%H:%M:%S')] $*"; }

if [ ! -d "$VENV" ]; then
    log "Creating venv at $VENV"
    python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

log "Installing from services/backend/requirements.txt"
pip install -q --upgrade pip
pip install -q -r services/backend/requirements.txt
pip install -q ruff==0.6.9 pytest-cov==7.1.0

log "Lint"
ruff check services/backend services/agent

log "Tests + coverage gate (80%)"
PYTHONPATH=. pytest services/backend/tests/ services/agent/tests/ \
    --cov=services/backend --cov=services/agent \
    --cov-report=term --cov-fail-under=80 "$@"

log "Done"
