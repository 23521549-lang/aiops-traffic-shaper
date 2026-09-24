"""One next action, and it has to be one a stranger can take.

The hero offered "Install the agent", and the install section said "Sign in,
open Agents, and add one." There is no self-service signup anywhere in this
product: tenants are created by an operator through POST /admin/v1/tenants,
and no route a stranger can reach asks for one. So the primary call to action
was an instruction whose first step is signing in to an account nobody can
create.

Spec 7 names this exact failure: a button pointing at a signup page that does
not exist is worse than admitting the constraint, because admitting
constraints is the only asset this page has.

The action cannot be a write. This page reads no DynamoDB, which is what lets
it sit behind a cache behaviour and cost nothing, and a public POST that
stores anything is an abuse surface on a 20 WCU account budget with no rate
limiting in front of it.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.core.config import settings
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.main import app


@pytest.fixture
def page(dynamo_resource):
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app, base_url="https://testserver").get("/").text


def _hero(page):
    return page[page.index("lp-cta"):page.index("lp-fineprint")]


def test_the_primary_action_is_one_a_stranger_can_take(page):
    """Not "install the agent", whose first step is signing in to an account
    that cannot be created."""
    hero = _hero(page)

    assert "btn-primary" in hero
    assert "request access" in hero.lower()


def test_the_action_routes_to_the_operator(page):
    assert f"mailto:{settings.access_request_email}" in page


def test_the_page_says_it_is_invite_only(page):
    """Spec 7. The page spends its credibility admitting constraints, and
    this is the constraint a reader hits first."""
    assert "invite" in page.lower()


def test_the_page_says_a_person_reads_the_requests(page):
    """One operator, by hand. A reader who expects an instant provisioning
    email and waits for one has been misled by omission."""
    lowered = page.lower()

    assert "one person" in lowered or "by hand" in lowered


def test_the_request_action_is_not_a_write(page):
    """A public POST that stores anything is an abuse surface on a free-tier
    write budget with no rate limiting in front of it, and it would make the
    page uncacheable besides."""
    hero = _hero(page)

    assert "<form" not in hero
    assert "hx-post" not in hero


def test_there_is_no_anonymous_route_that_stores_a_request(dynamo_resource):
    """The shape of the mistake this avoids: an endpoint a stranger can post
    to that writes a row."""
    from services.backend.main import app as real_app

    anonymous_writes = [
        r for r in real_app.routes
        if getattr(r, "path", "").startswith(("/ui/request", "/request",
                                              "/ui/access"))
    ]

    assert anonymous_writes == []


def test_sign_in_is_still_offered_for_people_who_have_an_account(page):
    assert "/ui/login" in page


def test_the_install_instructions_are_still_there(page):
    """They are what happens after access is granted, and a reader deciding
    whether to ask wants to know what they are agreeing to. They stop being
    the call to action; they do not stop existing."""
    assert "traffic-shaper connect" in page or "traffic-shaper run" in page


def test_nothing_promises_a_signup_that_does_not_exist(page):
    lowered = page.lower()

    assert "sign up" not in lowered
    assert "create an account" not in lowered
    assert "get started free" not in lowered


def test_the_page_still_reads_no_dynamodb_after_all_this(monkeypatch):
    """The property the whole design of this task is bent around."""
    from services.backend.core import tables

    def explode(*a, **kw):
        raise AssertionError("the landing page read DynamoDB")

    monkeypatch.setattr(tables._SimpleTable, "get", explode, raising=False)
    app.dependency_overrides.pop(get_dynamo_resource, None)

    assert TestClient(app, base_url="https://testserver").get("/").status_code == 200
