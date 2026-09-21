import json
import logging
import os
from dataclasses import asdict, replace

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.log_level import apply_log_level
from services.backend.core.tables import TenantsTable
from services.backend.ml import registry
from services.backend.ml.feature_engineering import collect_training_vectors
from services.backend.ml.registry import ModelMetadata
from services.backend.ml.training import MIN_TRAINING_SAMPLES, train_and_save
from services.backend.ml.validation import validate_model

logger = logging.getLogger(__name__)


def retrain_tenant(resource, tenant_id: str) -> ModelMetadata | None:
    """Retrain ONE tenant. Returns the promoted (or refused-but-staged) model's
    metadata, or None when there is too little data to train on.

    Training goes to `staging` and reaches `production` only through
    services/backend/ml/validation.py - the port of the old validator that
    ADR-002 listed as reuse material. Until Phase 6 that port was a backlog
    item and a model trained on a bad night of traffic went live unopposed.

    Exceptions are NOT caught here, on purpose: each caller decides what a
    failure means. The in-process loop isolates it; a Lambda worker lets it
    propagate so the invocation counts as an error, Lambda's async retry
    tries again, and the retrain-failed alarm sees it."""
    vectors = collect_training_vectors(resource, tenant_id)

    if len(vectors) < MIN_TRAINING_SAMPLES:
        logger.info("Skipping retrain: tenant=%s samples=%d (need %d)",
                    tenant_id, len(vectors), MIN_TRAINING_SAMPLES)
        return None

    staging_meta = train_and_save(resource, tenant_id, vectors, stage="staging")
    if staging_meta is None:
        return None

    staging_model = registry.load_model(resource, tenant_id, stage="staging")
    prod_model = registry.load_model(resource, tenant_id, stage="production")
    promote, reason = validate_model(staging_model, staging_meta, vectors,
                                      prod_model=prod_model)

    if not promote:
        # The staged model is kept, not discarded: it is the evidence for why
        # promotion was refused, and production keeps serving.
        logger.warning("Retrain NOT promoted: tenant=%s version=%s reason=%s",
                       tenant_id, staging_meta.version, reason)
        return staging_meta

    prod_meta = replace(staging_meta, stage="production")
    registry.save_model(resource, tenant_id, staging_model, prod_meta, stage="production")
    logger.info("Retrained and promoted: tenant=%s version=%s samples=%d (%s)",
                tenant_id, prod_meta.version, len(vectors), reason)
    return prod_meta


def retrain_all_tenants(resource) -> dict[str, ModelMetadata | None]:
    """Serial, in-process retrain of every tenant - for tests and local runs.
    Production does NOT use this; see handler() and dispatch_all().

    Each tenant is isolated. Before, an exception while training one tenant
    escaped the loop and every tenant after it in the scan went without a
    retrain that night, with a single anonymous Lambda error as the only
    trace. A failed tenant is now logged by name and left out of the result;
    None still means only "too little data"."""
    results: dict[str, ModelMetadata | None] = {}
    for tenant in TenantsTable(resource).list_all():
        tenant_id = tenant["tenant_id"]
        try:
            results[tenant_id] = retrain_tenant(resource, tenant_id)
        except Exception:
            logger.exception("Retrain FAILED: tenant=%s - other tenants continue", tenant_id)
    return results


def _lambda_invoke():
    import boto3
    return boto3.client("lambda").invoke


def dispatch_all(resource, invoke=None) -> dict:
    """Fan-out: one asynchronous invocation of this same function per tenant.

    The serial loop this replaces had two ceilings. Every tenant shared one
    15-minute Lambda timeout, measured at roughly two seconds a tenant, so a
    few hundred tenants would have hit it; and one tenant's exception stopped
    all the rest. Fanned out, each tenant retrains in its own invocation with
    its own timeout, failures stay with the tenant that caused them, tenants
    run in parallel, and Lambda's built-in async retry covers a transient
    failure. It stays free: async invocations are ordinary Lambda requests,
    one per tenant per night.

    InvocationType "Event" returns as soon as the request is queued, so the
    dispatcher finishes in well under a second whatever the tenant count."""
    invoke = invoke or _lambda_invoke()
    function_name = os.environ["AWS_LAMBDA_FUNCTION_NAME"]
    dispatched = []
    for tenant in TenantsTable(resource).list_all():
        tenant_id = tenant["tenant_id"]
        invoke(FunctionName=function_name, InvocationType="Event",
               Payload=json.dumps({"tenant_id": tenant_id}).encode())
        dispatched.append(tenant_id)
    logger.info("Retrain dispatched: tenants=%d", len(dispatched))
    return {"dispatched": dispatched}


def handler(event, context):
    """Two modes, one function.

    Scheduled (the nightly EventBridge rule, no tenant_id): dispatcher - fans
    out one async invocation per tenant and returns.

    Worker ({"tenant_id": ...}, sent by the dispatcher): retrains that single
    tenant. An exception is allowed to propagate so the invocation is recorded
    as an error, retried by Lambda, and seen by the retrain-failed alarm - with
    the tenant id in the log line next to it.

    Everything returned is plain JSON. The Lambda runtime json-encodes a
    handler's return value, and returning ModelMetadata dataclasses made every
    real run fail with Runtime.MarshalError after the training had finished.

    The API Lambda caches loaded models per warm container, and nothing here
    can invalidate that cache: a freshly promoted model reaches live traffic as
    the API's containers recycle. Documented since Stage 3, still true."""
    apply_log_level()
    resource = get_dynamo_resource()
    tenant_id = (event or {}).get("tenant_id")
    if tenant_id:
        meta = retrain_tenant(resource, tenant_id)
        return {"tenant_id": tenant_id,
                "result": asdict(meta) if meta is not None else None}
    return dispatch_all(resource)
