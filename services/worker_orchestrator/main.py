import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from services.worker_orchestrator.api.routes import blocklist, mitigate
from services.worker_orchestrator.core.config import settings
from services.worker_orchestrator.core.redis_client import close_redis, get_redis
from services.worker_orchestrator.orchestrator.cleanup import start_cleanup_scheduler
from services.worker_orchestrator.orchestrator.metrics import update_mitigation_metrics

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Worker Orchestrator starting up")

    redis = await get_redis()
    await redis.ping()
    logger.info(
        "Redis connection established: %s:%s",
        settings.redis_host,
        settings.redis_port,
    )

    await update_mitigation_metrics(redis)
    logger.info("Initial metrics snapshot taken")

    scheduler = start_cleanup_scheduler()

    yield

    logger.info("Worker Orchestrator shutting down")
    scheduler.shutdown(wait=False)
    await close_redis()
    logger.info("Redis connection closed")


app = FastAPI(
    title="Worker Orchestrator",
    description="AIOps mitigation and blocklist management service",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(
    mitigate.router,
    prefix="/api/v1",
    tags=["mitigation"],
)

app.include_router(
    blocklist.router,
    prefix="/api/v1",
    tags=["blocklist"],
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}
