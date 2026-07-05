import asyncio
import logging

from fastapi import APIRouter

from services.ai_engine.api.dependencies import InternalAuth
from services.ai_engine.core.config import settings
from services.ai_engine.ml.model import model_manager
from services.ai_engine.ml.registry import load_metadata

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/model/status")
async def model_status(_: None = InternalAuth) -> dict:
    meta = load_metadata("production")
    return {
        "model_ready":        model_manager.is_ready,
        "shadow_mode":        settings.shadow_mode,
        "version":            meta.version if meta else None,
        "trained_at":         meta.trained_at if meta else None,
        "training_samples":   meta.training_samples if meta else None,
        "contamination":      meta.contamination if meta else None,
        "score_mean":         meta.score_mean if meta else None,
        "score_std":          meta.score_std if meta else None,
    }


@router.post("/model/retrain")
async def trigger_retrain(_: None = InternalAuth) -> dict[str, str]:
    from services.ai_engine.ml.training import run_baseline_training
    logger.info("Manual retrain triggered via API")
    asyncio.create_task(run_baseline_training())
    return {"message": "Retrain job triggered in background"}


@router.post("/model/promote")
async def promote_model(_: None = InternalAuth) -> dict[str, str]:
    from services.ai_engine.ml.registry import (
        model_exists,
        promote_staging_to_production,
    )

    if not model_exists("staging"):
        return {"message": "No staging model available to promote"}

    promoted = promote_staging_to_production()
    if promoted:
        model_manager.reload()
        logger.info("Staging model promoted to production via API")
        return {"message": "Staging model promoted to production successfully"}

    return {"message": "Promotion failed — check logs for details"}
