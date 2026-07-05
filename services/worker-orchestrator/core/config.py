from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    internal_secret: str
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    namespace: str = "aiops"
    blocklist_configmap: str = "nginx-blocklist"
    tier1_ttl_seconds: int = 300
    tier2_ttl_seconds: int = 3600
    cleanup_interval_seconds: int = 60
    whitelist_key: str = "whitelist:ips"
    mitigation_key_prefix: str = "mitigation"

    class Config:
        env_file = ".env"


settings = Settings()
