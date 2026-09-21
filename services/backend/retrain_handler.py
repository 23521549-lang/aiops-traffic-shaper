import logging
import time
from dataclasses import asdict, replace

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import TenantsTable
from services.backend.ml.feature_engineering import collect_training_vectors
from services.backend.ml import registry
from services.backend.ml.registry import ModelMetadata
from services.backend.ml.training import MIN_TRAINING_SAMPLES, train_and_save
from services.backend.ml.validation import validate_model

logger = logging.getLogger(__name__)

# ~2/3 of Lambda's 15-minute timeout — a warning margin, not a hard cutoff.
_SLOW_RUN_WARNING_SECONDS = 600


def retrain_all_tenants(resource) -> dict[str, ModelMetadata | None]:
    """v1 is a serial loop over every tenant — explicitly acceptable per
    docs/PLAN.md Stage 7 given the small expected tenant count at launch.

    Training now goes to `staging` and reaches `production` only through
    services/backend/ml/validation.py, the port of the old
    ai_engine/ml/validator.py that ADR-002 listed as reuse-candidate
    material (the old file was removed in Phase 6 once ported). Until Phase 6 that port was a backlog item and this function
    wrote straight to production, so a model trained on a bad night of
    traffic went live unopposed."""
    start = time.time()
    results: dict[str, ModelMetadata | None] = {}

    for tenant in TenantsTable(resource).list_all():
        tenant_id = tenant["tenant_id"]
        vectors = collect_training_vectors(resource, tenant_id)

        if len(vectors) < MIN_TRAINING_SAMPLES:
            logger.info("Skipping retrain: tenant=%s samples=%d (need %d)",
                        tenant_id, len(vectors), MIN_TRAINING_SAMPLES)
            results[tenant_id] = None
            continue

        staging_meta = train_and_save(resource, tenant_id, vectors, stage="staging")
        if staging_meta is None:
            results[tenant_id] = None
            continue

        staging_model = registry.load_model(resource, tenant_id, stage="staging")
        prod_model = registry.load_model(resource, tenant_id, stage="production")
        promote, reason = validate_model(staging_model, staging_meta, vectors,
                                          prod_model=prod_model)

        if not promote:
            # The staged model is kept, not discarded: it is the evidence for
            # why promotion was refused, and production keeps serving.
            logger.warning("Retrain NOT promoted: tenant=%s version=%s reason=%s",
                           tenant_id, staging_meta.version, reason)
            results[tenant_id] = staging_meta
            continue

        prod_meta = replace(staging_meta, stage="production")
        registry.save_model(resource, tenant_id, staging_model, prod_meta,
                            stage="production")
        results[tenant_id] = prod_meta
        logger.info("Retrained and promoted: tenant=%s version=%s samples=%d (%s)",
                     tenant_id, prod_meta.version, len(vectors), reason)

    elapsed = time.time() - start
    logger.info("Daily retrain run complete: tenants=%d elapsed=%.1fs", len(results), elapsed)
    if elapsed > _SLOW_RUN_WARNING_SECONDS:
        logger.warning(
            "Retrain run took %.1fs, approaching Lambda's 15-minute timeout — "
            "switch to per-tenant fan-out (EventBridge/Step Functions) per "
            "docs/PLAN.md Stage 7's own flagged threshold", elapsed,
        )
    return results


def handler(event, context):
    """EventBridge scheduled-rule entry point — a separate Lambda function
    from main.py's API handler, deployed and invoked independently. Note:
    ModelManager's warm-container cache (Stage 3) lives in the API
    Lambda's own execution environment, not this one — there is no shared
    memory between separate Lambda functions, so this handler cannot and
    does not attempt to invalidate that cache. A freshly promoted model
    reaches API traffic only once the API Lambda's warm containers recycle
    naturally, exactly as Stage 3 already documented."""
    resource = get_dynamo_resource()
    results = retrain_all_tenants(resource)
    # The Lambda runtime JSON-serialises whatever this returns, and
    # ModelMetadata is a dataclass json cannot encode. Returning the rich
    # objects made every real invocation fail at the very last step with
    # Runtime.MarshalError - AFTER the training work had already happened -
    # so the retrain-failed alarm would fire nightly on runs that had in fact
    # promoted a model. retrain_all_tenants keeps returning the objects for
    # in-process callers; only the Lambda boundary flattens them.
    #
    # A skipped tenant stays an explicit null rather than a missing key, so a
    # tenant with too little data is distinguishable from one never visited.
    return {
        "tenants": {
            tenant_id: (asdict(meta) if meta is not None else None)
            for tenant_id, meta in results.items()
        },
    }
