from fastapi import FastAPI
from mangum import Mangum

from services.backend.api.routes.admin_usage import router as admin_usage_router
from services.backend.api.routes.agent import router as agent_router
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.usage import record_invocation

app = FastAPI()
app.include_router(agent_router)
app.include_router(admin_usage_router)


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
