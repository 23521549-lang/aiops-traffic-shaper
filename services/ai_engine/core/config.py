from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    internal_secret: str
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    window_seconds: int = 5
    window_ttl_seconds: int = 10
    min_requests_threshold: int = 3
    shadow_mode: bool = True
    model_contamination: float = 0.01
    tier1_score_threshold: float = -0.1
    tier2_score_threshold: float = -0.3
    worker_orchestrator_url: str = "http://worker-orchestrator:8050"
    whitelist_redis_key: str = "whitelist:ips"
    reputation_redis_key: str = "reputation:blacklist"
    retrain_interval_hours: int = 24
    shadow_mode_hours: int = 24
    redis_stream_maxlen: int = 500000

    class Config:
        env_file = ".env"


settings = Settings()
