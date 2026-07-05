from fastapi import Security
from services.worker_orchestrator.core.security import verify_internal_token

InternalAuth = Security(verify_internal_token)
