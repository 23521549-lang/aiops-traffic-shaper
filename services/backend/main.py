import logging
import os

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from mangum import Mangum

from services.backend.api.routes.admin import router as admin_router
from services.backend.api.routes.admin_usage import router as admin_usage_router
from services.backend.api.routes.agent import router as agent_router
from services.backend.api.routes.dashboard import router as dashboard_router
from services.backend.core.config import settings
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.usage import record_invocation
from services.backend.ui.auth_pages import router as ui_auth_router
from services.backend.ui.control_platform import router as ui_control_platform_router
from services.backend.ui.csrf import CsrfError
from services.backend.ui.templates_env import templates
from services.backend.ui.dashboard import router as ui_dashboard_router
from services.backend.ui.public import router as ui_public_router
from services.backend.ui.static_files import router as ui_static_router

logger = logging.getLogger(__name__)

app = FastAPI()
app.include_router(agent_router)
app.include_router(admin_usage_router)
app.include_router(dashboard_router)
app.include_router(admin_router)
app.include_router(ui_auth_router)
app.include_router(ui_dashboard_router)
app.include_router(ui_control_platform_router)
app.include_router(ui_static_router)
app.include_router(ui_public_router)

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


# --- error pages ---------------------------------------------------------

# A browser asking for a page and a client asking for JSON want different
# things from a failure, and the product had neither: a 404 was FastAPI's
# bare {"detail": "Not Found"} and a 500 was a stack trace behind a generic
# gateway error. An error page is a product surface.
_HTML_ACCEPT = "text/html"


def _wants_html(request: Request) -> bool:
    if request.url.path.startswith(("/agent/v1", "/dashboard/v1", "/admin/v1")):
        return False
    return _HTML_ACCEPT in request.headers.get("accept", "")


def _error_page(request: Request, code: int, heading: str, detail: str,
                back_url: str = "/", back_label: str = "Go back") -> Response:
    return templates.TemplateResponse(request, "error.html", {
        "code": code, "heading": heading, "detail": detail,
        "back_url": back_url, "back_label": back_label,
        "request_id": request.headers.get("x-amzn-trace-id", ""),
        "theme": request.cookies.get("theme", ""),
    }, status_code=code)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if not _wants_html(request):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return _error_page(
        request, 404, "That page isn’t here.",
        "The link may be old, or the address may have a typo in it. "
        "Nothing is wrong with your account.",
    )


@app.exception_handler(500)
async def server_error(request: Request, exc):
    # Deliberately says nothing about what failed. The detail belongs in
    # CloudWatch, and the reference above is how to find it.
    logger.exception("unhandled error on %s", request.url.path)
    if not _wants_html(request):
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
    return _error_page(
        request, 500, "Something broke on our side.",
        "This is not something you did, and nothing you were looking at has "
        "changed. If it keeps happening, quote the reference below.",
    )


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
_UNMETERED_PATH_PREFIXES = ("/health", "/ready", "/ui/login", "/ui/logout", "/ui/static")


def _should_meter(request, response) -> bool:
    if request.url.path.startswith(_UNMETERED_PATH_PREFIXES):
        return False
    # "/" cannot be expressed as a prefix - every path starts with it - and it
    # is the most exposed URL the deployment has. Before it redirected it
    # answered 404, and that 404 was metered: a DynamoDB write per drive-by
    # request on the front door.
    if request.url.path == "/":
        return False
    # The landing page and the theme toggle are anonymous and read no
    # DynamoDB, which is what lets them sit behind a CloudFront cache
    # behaviour. Metering them would spend write capacity on traffic that
    # never reaches the application.
    if request.url.path.startswith("/ui/prefs/"):
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
        # Every script is same-origin under /ui/static/ and there is no
        # inline script anywhere, so no 'unsafe-inline' on either directive.
        "script-src 'self'; "
        # Tightened from 'self' 'unsafe-inline' (ADR-007). The only reason
        # it was ever loose was base.html's 116-line inline <style>, which
        # now lives in /ui/static/app.css. htmx would have re-opened it by
        # injecting its own indicator styles, so it is configured with
        # includeIndicatorStyles:false and those rules ship in app.css too.
        "style-src 'self'; "
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
    """Liveness only: the process started and can answer. It says nothing
    about whether this instance can serve a real request - see /ready.

    `version` is the Lambda version that actually answered. Traffic reaches
    this function through the `live` alias, which can be repointed in seconds
    to roll back, so "what is running?" stopped being the same question as
    "what was last deployed?". The runtime sets AWS_LAMBDA_FUNCTION_VERSION to
    the version behind the alias; outside Lambda there is none, and "local"
    says so rather than inventing one. A sequence number, not a commit id -
    nothing here helps an attacker that the version count would not."""
    return {"status": "healthy",
            "version": os.environ.get("AWS_LAMBDA_FUNCTION_VERSION", "local")}


# The readiness probe describes a table rather than reading from one.
# DescribeTable is a control-plane call: it consumes no read capacity, so a
# probe polled every few seconds costs nothing against the 25 RCU the whole
# architecture is budgeted to (ADR-002). A GetItem here would not.
_READINESS_TABLE = "Tenants"


def _dynamodb_ready(resource) -> bool:
    try:
        described = resource.meta.client.describe_table(TableName=_READINESS_TABLE)
    except (ClientError, BotoCoreError):
        return False
    return described["Table"]["TableStatus"] == "ACTIVE"


@app.get("/ready")
def ready(resource=Depends(get_dynamo_resource)):
    """Readiness: can this instance actually serve? Two dependencies decide
    that, and both have failed silently in this project's history.

    DynamoDB - in production the tables exist only because terraform
    created them; create_all_tables() is called by the test suite and by
    scripts/run_local.py, never by this application. (This docstring said
    the opposite for a long time.) Either way a deployment can be up and
    answering /health with no table behind it, which is what this checks.

    Cognito configuration - Phase 4 (H1) made authentication fail CLOSED when
    the pool id or app client id is unset. Such a deployment authenticates
    nobody while looking perfectly healthy; docs/deployment.md calls both
    values required, and this is what makes that claim checkable rather than
    a sentence somebody has to remember to read.
    """
    checks = {
        "dynamodb": _dynamodb_ready(resource),
        "cognito_config": bool(settings.cognito_user_pool_id
                               and settings.cognito_app_client_id),
    }
    if all(checks.values()):
        return {"status": "ready", "checks": checks}
    return JSONResponse(status_code=503,
                        content={"status": "not_ready", "checks": checks})


handler = Mangum(app)
