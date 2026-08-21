from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    internal_secret: str
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    namespace: str = "aiops"
    blocklist_configmap: str = "nginx-blocklist"
    ratelimit_configmap: str = "nginx-ratelimit"
    tier1_ttl_seconds: int = 300
    tier2_ttl_seconds: int = 3600
    cleanup_interval_seconds: int = 60
    whitelist_key: str = "whitelist:ips"
    mitigation_key_prefix: str = "mitigation"


settings = Settings()
