"""Run the whole backend on a laptop: no AWS account, no Cognito, no network.

    python scripts/run_local.py            # http://127.0.0.1:8000
    python scripts/run_local.py --no-model # start in shadow mode

What is real and what is local
------------------------------
Real, unmodified: every route, the CloudFront-free request path, CSRF, the
security headers, the per-tenant quota, feature engineering, IsolationForest
training and scoring, the validation gate - and AUTHENTICATION. The real
`_decode_and_verify` runs on every request: signature, audience, issuer,
expiry and token_use are all checked. Only the SOURCE of public keys is
swapped, from Cognito's JWKS URL to a key pair generated at start-up. A token
signed with any other key is rejected exactly as in production
(test_run_local.py proves it).

Local: DynamoDB is moto, in-process. Tokens are minted here and printed.

It cannot touch real AWS
------------------------
The machine this runs on may hold admin credentials for the production
account. Every AWS call is intercepted by moto, and the environment's
credentials are ALSO replaced with fake ones before anything starts, so if a
call ever escaped the mock it would fail to authenticate instead of reaching
production. It binds to 127.0.0.1 only, and it refuses to run anywhere
AWS_LAMBDA_FUNCTION_NAME is set. It lives in scripts/, which the build does
not package - only services/ ships.
"""
import argparse
import json
import os
import random
import secrets
import sys
import time
from dataclasses import dataclass, field

LOCAL_POOL_ID = "ap-southeast-1_local"
LOCAL_CLIENT_ID = "local-dev-client"
LOCAL_REGION = "ap-southeast-1"
LOCAL_KID = "local-dev-key"
TENANT_ID = "local-demo"
_TOKEN_TTL_SECONDS = 12 * 3600


def _neutralise_real_credentials() -> None:
    """Before boto3 is touched at all. If moto ever failed to intercept a
    call, these make it fail to authenticate rather than land on production."""
    for var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN",
                "AWS_SECURITY_TOKEN", "AWS_CONFIG_FILE",
                "AWS_SHARED_CREDENTIALS_FILE"):
        os.environ.pop(var, None)
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = LOCAL_REGION


@dataclass
class LocalSession:
    app: object
    tenant_id: str
    owner_token: str
    admin_token: str
    agent_key: str
    _mock: object = field(repr=False)
    _saved_settings: tuple = field(repr=False)

    def stop(self) -> None:
        from services.backend.api.cognito_auth import get_jwks
        from services.backend.core.config import settings
        from services.backend.ml.model import ModelManager

        self.app.dependency_overrides.pop(get_jwks, None)
        (settings.cognito_user_pool_id, settings.cognito_region,
         settings.cognito_app_client_id) = self._saved_settings
        ModelManager._cache.clear()
        self._mock.stop()


def _sign(private_pem: bytes, claims: dict) -> str:
    import jwt

    from services.backend.api.cognito_auth import _issuer

    now = int(time.time())
    full = {"token_use": "id", "aud": LOCAL_CLIENT_ID, "iss": _issuer(),
            "iat": now, "exp": now + _TOKEN_TTL_SECONDS, **claims}
    return jwt.encode(full, private_pem, algorithm="RS256", headers={"kid": LOCAL_KID})


def _seed_model(resource) -> None:
    """Enough normal traffic across enough 5-second buckets to clear the
    training minimum, then the real retrain path - validation gate included -
    so the model a laptop serves was produced exactly the way production's is."""
    from services.backend.ml.feature_engineering import record_batch
    from services.backend.retrain_handler import retrain_tenant

    class _Log:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    rnd = random.Random(7)
    paths = ["/", "/products", "/products/42", "/about", "/cart", "/checkout", "/blog"]
    uas = ["Mozilla/5.0 (Windows NT 10.0)", "Mozilla/5.0 (Macintosh)",
           "Mozilla/5.0 (iPhone)", "Mozilla/5.0 (Linux; Android 14)"]
    start = time.time() - 3600
    for bucket in range(8):
        logs = []
        for ip in {f"192.0.2.{rnd.randint(1, 250)}" for _ in range(25)}:
            ua = rnd.choice(uas)
            for _ in range(rnd.randint(2, 9)):
                logs.append(_Log(
                    remote_addr=ip, request_method="POST" if rnd.random() < 0.08 else "GET",
                    request_uri=rnd.choice(paths),
                    status="404" if rnd.random() < 0.03 else "200",
                    body_bytes_sent=str(rnd.randint(600, 6000)),
                    request_time=f"{rnd.uniform(0.02, 0.35):.3f}", http_user_agent=ua))
        record_batch(resource, TENANT_ID, logs, now=start + bucket * 6)
    if retrain_tenant(resource, TENANT_ID) is None:
        raise RuntimeError("seed model was not trained - too little synthetic data")


def start_session(seed_model: bool = True) -> LocalSession:
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        sys.exit("run_local.py refuses to run inside a Lambda function.")

    _neutralise_real_credentials()

    import boto3
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm
    from moto import mock_aws

    from services.backend.api.cognito_auth import get_jwks
    from services.backend.api.dependencies import hash_api_key
    from services.backend.core.config import settings
    from services.backend.core.tables import AgentsTable, TenantsTable, create_all_tables
    from services.backend.main import app
    from services.backend.ml.model import ModelManager

    mock = mock_aws()
    mock.start()
    ModelManager._cache.clear()

    resource = boto3.resource("dynamodb", region_name=LOCAL_REGION)
    create_all_tables(resource)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": LOCAL_KID, "alg": "RS256", "use": "sig"})
    jwks = {"keys": [jwk]}

    saved = (settings.cognito_user_pool_id, settings.cognito_region,
             settings.cognito_app_client_id)
    settings.cognito_user_pool_id = LOCAL_POOL_ID
    settings.cognito_region = LOCAL_REGION
    settings.cognito_app_client_id = LOCAL_CLIENT_ID
    app.dependency_overrides[get_jwks] = lambda: jwks

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    TenantsTable(resource).put(tenant_id=TENANT_ID, name="Local demo", status="active",
                               created_at=now)
    raw_key = secrets.token_urlsafe(24)
    AgentsTable(resource).put(tenant_id=TENANT_ID, agent_id="local-agent",
                              agent_label="local", registered_at=now, last_seen_at=now,
                              agent_version="local", api_key_hash=hash_api_key(raw_key),
                              status="active")

    if seed_model:
        _seed_model(resource)

    return LocalSession(
        app=app, tenant_id=TENANT_ID,
        owner_token=_sign(private_pem, {"custom:tenant_id": TENANT_ID,
                                        "email": "owner@local.test"}),
        admin_token=_sign(private_pem, {"cognito:groups": ["admin"],
                                        "email": "admin@local.test"}),
        agent_key=f"{TENANT_ID}.{raw_key}",
        _mock=mock, _saved_settings=saved,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-model", action="store_true",
                        help="start without a trained model (shadow mode)")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        sys.exit("uvicorn is not installed: pip install -r services/backend/requirements.txt")

    session = start_session(seed_model=not args.no_model)
    base = f"http://127.0.0.1:{args.port}"
    print(f"""
AI Traffic Shaper - LOCAL. In-process DynamoDB, no AWS, no network.

  UI login      {base}/ui/login   (paste a token below)
  Health        {base}/ready

  Tenant owner  {session.owner_token}

  Admin         {session.admin_token}

  Agent key     {session.agent_key}
                curl -X POST {base}/agent/v1/telemetry \\
                  -H 'content-type: application/json' \\
                  -H 'X-Agent-Key: {session.agent_key}' -d '{{"logs": []}}'

Tokens expire in 12 hours. Everything is discarded when this process exits.
""")
    # 127.0.0.1, never 0.0.0.0: this process holds a signing key and fake data,
    # and has no business being reachable from the rest of the network.
    uvicorn.run(session.app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
