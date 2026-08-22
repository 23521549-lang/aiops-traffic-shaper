from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cognito_user_pool_id: str = ""
    cognito_region: str = "ap-southeast-1"
    cognito_app_client_id: str = ""


settings = Settings()
