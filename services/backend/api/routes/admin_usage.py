from fastapi import APIRouter, Depends

from services.backend.api.cognito_auth import admin_auth
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.usage import UsageReport, get_usage_report

router = APIRouter()


@router.get("/admin/v1/usage", response_model=UsageReport, dependencies=[Depends(admin_auth)])
def usage(resource=Depends(get_dynamo_resource)) -> UsageReport:
    # Stage 5 shipped this route without auth, explicitly flagged there as
    # temporary pending Cognito. Closed here now that admin_auth exists.
    return get_usage_report(resource, date=None)
