import logging
import time

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import TenantsTable
from services.backend.ml.feature_engineering import collect_training_vectors
from services.backend.ml.registry import ModelMetadata
from services.backend.ml.training import MIN_TRAINING_SAMPLES, train_and_save

logger = logging.getLogger(__name__)

# ~2/3 of Lambda's 15-minute timeout — a warning margin, not a hard cutoff.
_SLOW_RUN_WARNING_SECONDS = 600


def retrain_all_tenants(resource) -> dict[str, ModelMetadata | None]:
    """v1 is a serial loop over every tenant — explicitly acceptable per
    docs/PLAN.md Stage 7 given the small expected tenant count at launch.
    Trains directly to 'production' with no staging/validation gate: a
    deliberate simplification, not an oversight. ADR-002's reuse table
    listed ai_engine/ml/validator.py (block-rate threshold + std
    regression checks against the previous production model) as
    reuse-candidate material, but porting it is real additional scope
    this stage's own goal doesn't require — tracked as a backlog item,
    not silently dropped."""
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

        meta = train_and_save(resource, tenant_id, vectors, stage="production")
        results[tenant_id] = meta
        logger.info("Retrained: tenant=%s version=%s samples=%d",
                     tenant_id, meta.version if meta else None, len(vectors))

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
    return retrain_all_tenants(resource)
