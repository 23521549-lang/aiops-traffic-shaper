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
    # Empty by default, not a plausible-looking placeholder. A deployment
    # that shipped `access@traffic-shaper.example` would put a mailto on the
    # front door that opens a mail client, sends, and bounces somewhere the
    # reader never sees - which is worse than publishing no address at all.
    access_request_email: str = ""


settings = Settings()
