from fastapi import APIRouter, Depends

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.usage import UsageReport, get_usage_report

router = APIRouter()


@router.get("/admin/v1/usage", response_model=UsageReport)
def usage(resource=Depends(get_dynamo_resource)) -> UsageReport:
    # NOT auth-gated yet — Cognito admin-group auth is a Stage 6
    # deliverable (docs/PLAN.md). Do not expose this route in a real
    # deployment before Stage 6 adds that dependency.
    return get_usage_report(resource, date=None)
