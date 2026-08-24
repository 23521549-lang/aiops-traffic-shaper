from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from mangum import Mangum

from services.backend.api.routes.admin import router as admin_router
from services.backend.api.routes.admin_usage import router as admin_usage_router
from services.backend.api.routes.agent import router as agent_router
from services.backend.api.routes.dashboard import router as dashboard_router
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.usage import record_invocation
from services.backend.ui.auth_pages import router as ui_auth_router
from services.backend.ui.control_platform import router as ui_control_platform_router
from services.backend.ui.csrf import CsrfError
from services.backend.ui.dashboard import router as ui_dashboard_router

app = FastAPI()
app.include_router(agent_router)
app.include_router(admin_usage_router)
app.include_router(dashboard_router)
app.include_router(admin_router)
app.include_router(ui_auth_router)
app.include_router(ui_dashboard_router)
app.include_router(ui_control_platform_router)

_UI_PAGE_PREFIXES = ("/dashboard/ui", "/admin/ui")


@app.exception_handler(HTTPException)
async def ui_auth_redirect_handler(request: Request, exc: HTTPException):
    """The JSON API routes (/agent/v1, /dashboard/v1, /admin/v1) keep
    FastAPI's default JSON error body — untouched here. Only the Stage 9
    HTML pages under /dashboard/ui and /admin/ui get a browser-friendly
    redirect to the login page on 401/403, instead of a JSON error body a
    human looking at a web page would never expect to see. An AJAX call
    from this page's own interactions.js (X-UI-AJAX header) gets an
    X-UI-Redirect response header instead of a raw redirect, since a
    fetch() follows redirects transparently and the page needs to know to
    navigate itself."""
    if exc.status_code in (401, 403):
        # M8: record the real reason before the UI rewrite hides it — a 401
        # turned into a 302 must still count as unauthenticated, not as work.
        request.state.auth_failed = True

    is_ui_page = request.url.path.startswith(_UI_PAGE_PREFIXES)
    if is_ui_page and exc.status_code in (401, 403) and not isinstance(exc, CsrfError):
        if request.headers.get("X-UI-AJAX"):
            response = Response(status_code=200)
            response.headers["X-UI-Redirect"] = "/ui/login"
            return response
        return RedirectResponse(url="/ui/login", status_code=302)
    # exc.headers must survive: the 429 carries Retry-After, and returning it
    # without that header leaves an agent with no basis for a backoff.
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                        headers=getattr(exc, "headers", None))


def _resolve_resource(request):
    """Middleware runs outside FastAPI's Depends() call graph, so it does
    NOT automatically honor app.dependency_overrides the way a route
    parameter does — found while wiring this in: a naive
    `record_invocation(get_dynamo_resource())` call here would call the
    real (unreachable in tests) resource even when a test has overridden
    get_dynamo_resource for every route. Checking dependency_overrides
    manually keeps middleware and routes consistent under the same test
    setup."""
    override = request.app.dependency_overrides.get(get_dynamo_resource)
    return override() if override else get_dynamo_resource()


# M8 — paths that must never cost a DynamoDB write: liveness probes run
# constantly, and the login/static endpoints are reachable before any
# credential exists, so metering them just hands an anonymous caller a lever
# on the free-tier ceiling this whole product is built around.
_UNMETERED_PATH_PREFIXES = ("/health", "/ui/login", "/ui/logout", "/ui/static")


def _should_meter(request, response) -> bool:
    if request.url.path.startswith(_UNMETERED_PATH_PREFIXES):
        return False
    if response.status_code in (401, 403, 429):
        return False
    # Set by the exception handler above, for UI paths whose 401 has already
    # been rewritten into a 302 by the time this middleware sees the response.
    return not getattr(request.state, "auth_failed", False)


@app.middleware("http")
async def track_usage(request, call_next):
    """M8: this used to record one DynamoDB write for EVERY request, including
    rejected ones. The Lambda Function URL is public and has no AWS-native
    rate limiting (ADR-002's accepted residual risk), so unauthenticated junk
    traffic could burn the account's Always-Free write quota. Metering now
    covers authenticated work only. This is a mitigation, not a cure — a
    caller holding valid credentials can still spend quota; edge rate limiting
    remains the real answer, and remains out of scope for the 0-cost model."""
    response = await call_next(request)
    if _should_meter(request, response):
        record_invocation(_resolve_resource(request))
    return response


# M6 — the app previously shipped no security headers whatsoever.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Lambda Function URLs are HTTPS-only, so HSTS costs nothing here.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": (
        "default-src 'self'; "
        # The UI's only script is /ui/static/interactions.js, served from this
        # same origin — no inline script, so no 'unsafe-inline' here. The
        # inline <style> block in base.html is why style-src needs it.
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    for header, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


@app.get("/health")
def health():
    return {"status": "healthy"}


handler = Mangum(app)
