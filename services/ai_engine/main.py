import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from services.ai_engine.api.routes import telemetry, whitelist, model
from services.ai_engine.core.config import settings
from services.ai_engine.core.redis_client import close_redis, get_redis
from services.ai_engine.ml.model import model_manager
from services.ai_engine.ml.monitoring import (
    model_monitor,
    shadow_mode_active,
)
from services.ai_engine.ml.training import start_training_scheduler

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("AI Engine starting up")
    logger.info("Shadow mode: %s", settings.shadow_mode)

    redis = await get_redis()
    await redis.ping()
    logger.info(
        "Redis connection established: %s:%s",
        settings.redis_host,
        settings.redis_port,
    )

    loaded = model_manager.load()
    if loaded:
        logger.info("Production model loaded successfully")
        shadow_mode_active.set(0)
        model_monitor.update_model_version_metric()
    else:
        logger.info("No production model found — shadow mode active")
        shadow_mode_active.set(1)

    scheduler = start_training_scheduler()

    yield

    logger.info("AI Engine shutting down")
    scheduler.shutdown(wait=False)
    await close_redis()
    logger.info("Redis connection closed")


app = FastAPI(
    title="AI Engine",
    description="AIOps AI inference and feature engineering service",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(
    telemetry.router,
    prefix="/api/v1",
    tags=["telemetry"],
)

app.include_router(
    whitelist.router,
    prefix="/api/v1",
    tags=["whitelist"],
)

app.include_router(
    model.router,
    prefix="/api/v1",
    tags=["model"],
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}
