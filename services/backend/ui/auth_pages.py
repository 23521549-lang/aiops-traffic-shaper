from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from services.backend.api.cognito_auth import _decode_and_verify, get_jwks
from services.backend.ui.templates_env import templates

router = APIRouter()

_STATIC_DIR = Path(__file__).parent / "static"


@router.get("/ui/static/interactions.js")
def interactions_js():
    return FileResponse(_STATIC_DIR / "interactions.js", media_type="application/javascript")


@router.get("/ui/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/ui/login")
def login_submit(request: Request, id_token: str = Form(...), jwks: dict = Depends(get_jwks)):
    try:
        claims = _decode_and_verify(id_token, jwks)
    except Exception:
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid or expired token."}, status_code=401,
        )

    if "admin" in claims.get("cognito:groups", []):
        destination = "/admin/ui"
    elif claims.get("custom:tenant_id"):
        destination = "/dashboard/ui"
    else:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Token has neither an admin group nor a tenant_id claim."},
            status_code=401,
        )

    response = RedirectResponse(url=destination, status_code=302)
    # secure=True: real deployment is always HTTPS (Lambda Function URLs
    # enforce TLS) — tests use an https:// TestClient base_url so this
    # cookie still round-trips locally without weakening it for real use.
    response.set_cookie("id_token", id_token, httponly=True, samesite="lax", secure=True, max_age=3600)
    return response


@router.get("/ui/logout")
def logout():
    response = RedirectResponse(url="/ui/login", status_code=302)
    response.delete_cookie("id_token")
    return response
