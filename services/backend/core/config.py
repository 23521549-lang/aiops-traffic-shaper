from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cognito_user_pool_id: str = ""
    cognito_region: str = "ap-southeast-1"
    cognito_app_client_id: str = ""

    # Where a stranger's request for access goes. There is no self-service
    # signup: an operator creates tenants by hand, so the landing page's one
    # call to action is an email to that operator rather than a form that
    # writes. A public write endpoint would be an abuse surface on a 20 WCU
    # account budget with no rate limiting in front of it, and it would make
    # the page uncacheable, which is what keeps it free.
    access_request_email: str = "access@traffic-shaper.example"


settings = Settings()
