"""Signing in with an email and a password, instead of a pasted JWT.

The login screen asked people to paste a 1074-character Cognito ID token
into a password field, and the template said so itself: "Known v1 gap".
A person who can obtain that token already has AWS CLI access; the person
this screen exists for cannot get past it.

It also blocked tenant creation outright. `AdminCreateUser` emails a
temporary password, and the product gave the recipient no way to use one —
they would have had to run `initiate-auth` themselves, handle the
`NEW_PASSWORD_REQUIRED` challenge with `RespondToAuthChallenge`, and paste
the result. A credential nobody can spend is not an onboarding flow.

Nothing new is needed on the Cognito side: `ALLOW_USER_PASSWORD_AUTH` has
been enabled on the app client since it was created and used by nothing, and
`generate_secret = false` means there is no client secret to store.

The password reaches this backend in the request body and is forwarded
straight to Cognito. That is a real difference from a hosted redirect flow,
where the password would never touch this code at all, and the mitigations
are stated rather than assumed: it is never logged, never stored, never
placed in a URL, and the one endpoint that accepts it is the only
unauthenticated write surface in the product.
"""
import logging

import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException

from services.backend.core.config import settings

logger = logging.getLogger(__name__)

# Cognito's own wording is accurate but unhelpful to a person who has just
# mistyped a password, and several distinct failures collapse into one code.
# Deliberately identical for "no such user" and "wrong password": telling an
# unauthenticated caller which one it was is a user-enumeration oracle.
_GENERIC = "That email and password did not match."

_MESSAGES = {
    "NotAuthorizedException": _GENERIC,
    "UserNotFoundException": _GENERIC,
    "UserNotConfirmedException": "This account has not been confirmed yet. Check your email.",
    "PasswordResetRequiredException": "Your password must be reset before you can sign in.",
    "TooManyRequestsException": "Too many attempts. Wait a minute and try again.",
    "LimitExceededException": "Too many attempts. Wait a minute and try again.",
}


def get_cognito_client():
    """A dependency so tests can substitute it. Real callers get a plain
    boto3 client against the configured region."""
    return boto3.client("cognito-idp", region_name=settings.cognito_region)


def _fail(code: str) -> HTTPException:
    return HTTPException(status_code=401, detail=_MESSAGES.get(code, _GENERIC))


def start_password_login(client, email: str, password: str) -> dict:
    """Returns either {"id_token": ...} or {"challenge": ..., "session": ...}.

    `NEW_PASSWORD_REQUIRED` is the normal first sign-in for a user created
    by an administrator, which is every user this product will ever have
    until self-service signup exists — so it is a main path, not an edge
    case.
    """
    try:
        resp = client.initiate_auth(
            ClientId=settings.cognito_app_client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        # Logged without the email: a failed-login log that names accounts
        # is a list of valid accounts for anyone who reaches the logs.
        logger.info("password login refused: %s", code)
        raise _fail(code) from None

    challenge = resp.get("ChallengeName")
    if challenge == "NEW_PASSWORD_REQUIRED":
        return {"challenge": challenge, "session": resp["Session"], "email": email}
    if challenge:
        # MFA and the rest are not configured on this pool. Failing loudly
        # beats pretending the sign-in worked.
        raise HTTPException(
            status_code=401,
            detail=f"This account needs a sign-in step this console cannot do yet ({challenge}).")

    result = resp.get("AuthenticationResult") or {}
    if not result.get("IdToken"):
        raise HTTPException(status_code=401, detail=_GENERIC)
    return {"id_token": result["IdToken"]}


def complete_new_password(client, email: str, session: str, new_password: str) -> dict:
    try:
        resp = client.respond_to_auth_challenge(
            ClientId=settings.cognito_app_client_id,
            ChallengeName="NEW_PASSWORD_REQUIRED",
            Session=session,
            ChallengeResponses={"USERNAME": email, "NEW_PASSWORD": new_password},
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "InvalidPasswordException":
            raise HTTPException(
                status_code=400,
                detail=("That password does not meet the policy: at least 12 characters, "
                        "with an uppercase letter, a lowercase letter and a digit.")) from None
        logger.info("set-password refused: %s", code)
        raise _fail(code) from None

    result = resp.get("AuthenticationResult") or {}
    if not result.get("IdToken"):
        raise HTTPException(status_code=401, detail=_GENERIC)
    return {"id_token": result["IdToken"]}
