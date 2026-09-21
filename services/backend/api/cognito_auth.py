import json
import urllib.request
from functools import lru_cache

from fastapi import Cookie, Depends, Header, HTTPException
import jwt
from jwt.algorithms import RSAAlgorithm

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


def _extract_token(authorization: str | None, id_token_cookie: str | None,
                   x_id_token: str | None = None) -> str:
    """Three sources, all funnelling into the same `_decode_and_verify`, so
    verification is identical however the token arrived.

    Bearer is checked first and X-Id-Token second, which sounds backwards and
    is not: in production the Authorization header holds CloudFront's SigV4
    signature, which does not begin with "Bearer ", so it falls through. The
    order costs nothing and keeps every direct-to-origin caller working
    unchanged.

    X-Id-Token is the one that matters in production. Since
    ADR-005 every request reaches this app through CloudFront, whose origin
    access control signs the origin request with SigV4 — and signing means
    REPLACING the Authorization header with its own signature. The alternative
    OAC setting, no-override, is worse: it forwards the viewer's Authorization
    and then does not sign at all, which an AWS_IAM function URL rejects. There
    is no OAC configuration in which a Bearer token reaches this function.
    X-Id-Token is a header CloudFront has no opinion about, so it survives.

    Found the hard way: a real agent registering against the real deployment
    sent a valid token and got 401 "Missing credentials" back, because the
    application never saw one.

    Authorization: Bearer still works for anything talking to the origin
    directly — local runs, and callers holding AWS credentials of their own.

    The cookie is Stage 9's browser UI: a plain HTML page navigation cannot
    attach any custom header, only JS/fetch can, so server-rendered pages need
    it to work at all.

    The isinstance guards are not defensive noise. These functions are FastAPI
    dependencies with Header()/Cookie() defaults, and the unit tests call them
    directly - so an argument nobody passed arrives as a Header OBJECT, not as
    None, and is perfectly truthy. Checking for a real string is what keeps the
    direct-call and through-FastAPI paths behaving the same."""
    if isinstance(authorization, str) and authorization.startswith("Bearer "):
        return authorization[len("Bearer "):]
    if isinstance(x_id_token, str) and x_id_token:
        return x_id_token
    if isinstance(id_token_cookie, str) and id_token_cookie:
        return id_token_cookie
    raise HTTPException(status_code=401, detail="Missing credentials")


def _issuer() -> str:
    return (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
        f"{settings.cognito_user_pool_id}"
    )


def _decode_and_verify(id_token: str, jwks: dict) -> dict:
    # Phase 4 / H1 — fail closed. The previous version passed
    # `options={"verify_aud": bool(settings.cognito_app_client_id)}`, so an
    # UNCONFIGURED deployment (app_client_id defaults to "") silently turned
    # audience checking off and accepted any token signed by any key in the
    # JWKS. An unconfigured deployment must authenticate nobody instead.
    if not settings.cognito_user_pool_id or not settings.cognito_app_client_id:
        raise HTTPException(status_code=401, detail="Authentication is not configured")

    try:
        unverified_header = jwt.get_unverified_header(id_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed token")

    key = next((k for k in jwks.get("keys", []) if k.get("kid") == unverified_header.get("kid")), None)
    if key is None:
        raise HTTPException(status_code=401, detail="Unknown signing key")

    try:
        public_key = RSAAlgorithm.from_jwk(json.dumps(key))
    except Exception:
        raise HTTPException(status_code=401, detail="Unknown signing key")

    try:
        claims = jwt.decode(
            id_token,
            public_key,
            # Phase 4 / H2 — pinned, never read from the token header NOR from
            # the JWKS entry. Cognito only ever signs with RS256; deriving the
            # algorithm from data is the classic JWT algorithm-confusion
            # foot-gun. Library is PyJWT, not python-jose — see docs/adr/
            # 003-jwt-library.md: jose pins pyasn1<0.5.0 (4 CVEs, fixed only
            # in 0.6.3+) and drags in ecdsa, whose advisory has no fix at all.
            algorithms=["RS256"],
            audience=settings.cognito_app_client_id,
            issuer=_issuer(),
            options={"verify_aud": True, "verify_iss": True, "verify_exp": True},
        )
    except Exception:
        # Phase 4 / L9 — the raw jose exception used to be echoed back to the
        # caller, leaking library internals and verification specifics.
        raise HTTPException(status_code=401, detail="Invalid token")

    # Phase 4 / H1 — Cognito signs ID tokens AND access tokens with the same
    # pool key, and access tokens also carry `cognito:groups`. Without this
    # check an access token — far more widely exposed, since it is handed to
    # resource servers — passes admin_auth. Only ID tokens are accepted.
    if claims.get("token_use") != "id":
        raise HTTPException(status_code=401, detail="Invalid token")

    return claims


def dashboard_auth(authorization: str | None = Header(default=None),
                    x_id_token: str | None = Header(default=None),
                    id_token: str | None = Cookie(default=None),
                    jwks: dict = Depends(get_jwks)) -> str:
    claims = _decode_and_verify(_extract_token(authorization, id_token, x_id_token), jwks)
    tenant_id = claims.get("custom:tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Token missing tenant_id claim")
    return tenant_id


def admin_auth(authorization: str | None = Header(default=None),
               x_id_token: str | None = Header(default=None),
               id_token: str | None = Cookie(default=None),
               jwks: dict = Depends(get_jwks)) -> None:
    claims = _decode_and_verify(_extract_token(authorization, id_token, x_id_token), jwks)
    groups = claims.get("cognito:groups", [])
    if "admin" not in groups:
        raise HTTPException(status_code=403, detail="Admin group membership required")
