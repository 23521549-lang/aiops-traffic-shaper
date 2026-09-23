from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from services.backend.api.cognito_auth import _decode_and_verify, get_jwks
from services.backend.api.cognito_login import (
    complete_new_password, get_cognito_client, start_password_login,
)
from services.backend.ui.csrf import CSRF_COOKIE_NAME, new_csrf_token
from services.backend.ui.templates_env import templates

router = APIRouter()


@router.get("/ui/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(request, "login.html", {"error": error})


def _destination(claims: dict) -> str | None:
    if "admin" in claims.get("cognito:groups", []):
        return "/admin/ui"
    if claims.get("custom:tenant_id"):
        return "/dashboard/ui"
    return None


def _signed_in(id_token: str, destination: str) -> RedirectResponse:
    response = RedirectResponse(url=destination, status_code=302)
    # secure=True: real deployment is always HTTPS (Lambda Function URLs
    # enforce TLS) — tests use an https:// TestClient base_url so this
    # cookie still round-trips locally without weakening it for real use.
    response.set_cookie("id_token", id_token, httponly=True, samesite="lax",
                        secure=True, max_age=3600)
    # M7 introduced this as NOT httponly, because the hand-written helper
    # read it out of document.cookie to echo into the X-CSRF-Token header.
    # htmx takes it from a server-rendered hx-headers attribute instead
    # (ADR-007), so the exposure bought nothing and is now closed. It was
    # never a credential — but a token no script can read cannot be
    # exfiltrated by one either.
    response.set_cookie(CSRF_COOKIE_NAME, new_csrf_token(), httponly=True,
                        samesite="lax", secure=True, max_age=3600)
    return response


def _error(request: Request, message: str, status: int = 401):
    return templates.TemplateResponse(
        request, "login.html", {"error": message}, status_code=status)


@router.post("/ui/login")
def login_submit(request: Request,
                 email: str = Form(default=""),
                 password: str = Form(default=""),
                 id_token: str = Form(default=""),
                 jwks: dict = Depends(get_jwks),
                 cognito=Depends(get_cognito_client)):
    """Two ways in, on purpose.

    Email and password is the one a person uses. The pasted ID token stays
    because the agent CLI's documented `register --token` workflow hands one
    out, and removing it would break an install flow that is already
    published — but it is no longer the only option, and no longer the
    default on the page.

    This is the one POST in the product that must carry a real request body.
    A password in a query string lands in CloudFront access logs, Referer
    headers and browser history. It is therefore also the only caller of
    signed-post.js, which computes the payload hash CloudFront's OAC
    requires (ADR-005, ADR-007).
    """
    if email and password:
        result = start_password_login(cognito, email, password)
        if result.get("challenge"):
            # An administrator-created user signing in for the first time.
            # Until self-service signup exists that is every user this
            # product will ever have, so it is a main path, not an edge case.
            return templates.TemplateResponse(request, "login_new_password.html", {
                "email": email, "session": result["session"],
            })
        id_token = result["id_token"]

    if not id_token:
        return _error(request, "Enter your email and password.", status=400)

    try:
        claims = _decode_and_verify(id_token, jwks)
    except Exception:
        return _error(request, "Invalid or expired token.")

    destination = _destination(claims)
    if destination is None:
        return _error(request,
                      "Token has neither an admin group nor a tenant_id claim.")
    return _signed_in(id_token, destination)


@router.post("/ui/login/new-password")
def set_new_password(request: Request,
                     email: str = Form(...),
                     session: str = Form(...),
                     new_password: str = Form(...),
                     confirm_password: str = Form(default=""),
                     jwks: dict = Depends(get_jwks),
                     cognito=Depends(get_cognito_client)):
    """Completes the NEW_PASSWORD_REQUIRED challenge.

    `session` is Cognito's opaque single-use handle, carried in a hidden
    field. It is not a credential on its own: it is usable only with the
    username it was issued for, and only once.
    """
    if new_password != confirm_password:
        return templates.TemplateResponse(request, "login_new_password.html", {
            "email": email, "session": session,
            "error": "The two passwords do not match.",
        }, status_code=400)

    result = complete_new_password(cognito, email, session, new_password)
    claims = _decode_and_verify(result["id_token"], jwks)
    destination = _destination(claims)
    if destination is None:
        return _error(request,
                      "This account is not attached to a tenant yet. "
                      "Ask whoever invited you.")
    return _signed_in(result["id_token"], destination)


@router.get("/ui/logout")
def logout():
    response = RedirectResponse(url="/ui/login", status_code=302)
    response.delete_cookie("id_token")
    response.delete_cookie(CSRF_COOKIE_NAME)
    return response
