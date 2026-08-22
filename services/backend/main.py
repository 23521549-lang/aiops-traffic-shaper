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
    is_ui_page = request.url.path.startswith(_UI_PAGE_PREFIXES)
    if is_ui_page and exc.status_code in (401, 403):
        if request.headers.get("X-UI-AJAX"):
            response = Response(status_code=200)
            response.headers["X-UI-Redirect"] = "/ui/login"
            return response
        return RedirectResponse(url="/ui/login", status_code=302)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


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


@app.middleware("http")
async def track_usage(request, call_next):
    response = await call_next(request)
    record_invocation(_resolve_resource(request))
    return response


@app.get("/health")
def health():
    return {"status": "healthy"}


handler = Mangum(app)
