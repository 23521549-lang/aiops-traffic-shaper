from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
from services.worker_orchestrator.core.config import settings

api_key_header = APIKeyHeader(name="X-Internal-Token", auto_error=False)


async def verify_internal_token(api_key: str = Security(api_key_header)) -> None:
    if not api_key or api_key != settings.internal_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Internal-Token header",
        )
