import re

from pydantic import BaseModel, Field, field_validator


class Tenant(BaseModel):
    tenant_id: str
    # Optional because it always was in practice. There is no create-tenant
    # route anywhere in the product, so every tenant that exists was written
    # directly to DynamoDB — and the one in production carries a `note` and
    # no `name`. Declaring it required asserted a guarantee the system never
    # made, and 500'd both admin pages on real data.
    name: str | None = None
    status: str
    created_at: str | None = None


class AgentSummary(BaseModel):
    tenant_id: str
    agent_id: str
    # Captured at registration and then dropped from this schema, so the
    # console showed a bare uuid for a machine its owner had already named.
    agent_label: str | None = None
    agent_version: str | None = None
    status: str
    last_seen_at: str


class TenantCreateRequest(BaseModel):
    # Required here even though Tenant.name stays optional. The optional
    # field exists for rows written before this route did — including the
    # one in production that 500'd both admin pages. Nothing created from
    # here should ever be nameless again.
    name: str = Field(min_length=1, max_length=100)
    contact_email: str

    @field_validator("contact_email")
    @classmethod
    def looks_like_an_email(cls, v: str) -> str:
        """Deliberately not pydantic's EmailStr.

        EmailStr pulls in `email-validator`, a new dependency on a package
        already at 200MB of a 250MB limit and a second thing for pip-audit
        to cover — to gain RFC-precise validation of a value Cognito
        validates properly anyway, and rejects on its own terms if it is
        wrong. A shape check here catches the typo; Cognito is the
        authority."""
        v = v.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("That does not look like an email address.")
        return v
    # Supplied by the new-tenant form as a hidden field, which is what makes
    # a double submit collide on attribute_not_exists and read as "already
    # created" instead of silently making a second tenant.
    tenant_id: str | None = None


class TenantCreateResponse(BaseModel):
    tenant_id: str
    name: str
    status: str
    created_at: str
    first_user_email: str
    password_delivery: str
