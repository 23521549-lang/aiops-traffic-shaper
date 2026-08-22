import json
import urllib.request
from functools import lru_cache

from fastapi import Depends, Header, HTTPException
from jose import jwt

from services.backend.core.config import settings


def _jwks_url() -> str:
    return (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
        f"{settings.cognito_user_pool_id}/.well-known/jwks.json"
    )


@lru_cache(maxsize=1)
def _fetch_jwks(url: str) -> dict:
    with urllib.request.urlopen(url) as resp:  # pragma: no cover — real AWS only
        return json.loads(resp.read())


def get_jwks() -> dict:
    """A separate FastAPI dependency, not inlined into the auth functions
    below — this is the ONLY piece that talks to real Cognito (unreachable
    in this environment and in tests). Tests override this with a locally
    generated JWKS instead of faking away token verification itself, so
    the actual `jwt.decode(...)` signature-checking code path in
    `_decode_and_verify` runs for real against a real (test) keypair, not
    a stubbed-out "assume it's valid" shortcut."""
    return _fetch_jwks(_jwks_url())


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    return authorization[len("Bearer "):]


def _decode_and_verify(id_token: str, jwks: dict) -> dict:
    try:
        unverified_header = jwt.get_unverified_header(id_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed token")

    key = next((k for k in jwks.get("keys", []) if k.get("kid") == unverified_header.get("kid")), None)
    if key is None:
        raise HTTPException(status_code=401, detail="Unknown signing key")

    try:
        return jwt.decode(
            id_token,
            key,
            algorithms=[key["alg"]],
            audience=settings.cognito_app_client_id or None,
            options={"verify_aud": bool(settings.cognito_app_client_id)},
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def dashboard_auth(authorization: str | None = Header(default=None),
                    jwks: dict = Depends(get_jwks)) -> str:
    claims = _decode_and_verify(_extract_bearer(authorization), jwks)
    tenant_id = claims.get("custom:tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Token missing tenant_id claim")
    return tenant_id


def admin_auth(authorization: str | None = Header(default=None),
               jwks: dict = Depends(get_jwks)) -> None:
    claims = _decode_and_verify(_extract_bearer(authorization), jwks)
    groups = claims.get("cognito:groups", [])
    if "admin" not in groups:
        raise HTTPException(status_code=403, detail="Admin group membership required")
