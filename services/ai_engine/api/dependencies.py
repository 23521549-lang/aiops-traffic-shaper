from fastapi import Security
from services.ai_engine.core.security import verify_internal_token

InternalAuth = Security(verify_internal_token)
