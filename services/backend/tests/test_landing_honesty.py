"""The page that spends its credibility admitting limits.

`_HERO_SHAPE` is forty-eight hardcoded floats, and the page captioned them as
twenty-four hours of a real site's traffic with two specific enforcement
events in it. No such site exists and no such event happened.

Every other claim on this page has been checked against the code at some
point - `test_landing_claims.py` exists because four of them had rotted into
fiction on the first instruction a new customer follows. The picture was never
checked, because a chart is not a sentence.

Two other properties are pinned here because both are the kind of thing a
later tidy-up removes without noticing: the section that says what the product
cannot do, and the fact that this page reads no DynamoDB.
"""
import inspect

import pytest
from fastapi.testclient import TestClient

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.main import app


@pytest.fixture
def page(dynamo_resource):
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app, base_url="https://testserver").get("/").text


# --- the picture -----------------------------------------------------------


def test_the_example_chart_says_it_is_an_example(page):
    """Not buried in small print underneath: on the chart, where a reader who
    reads nothing else still sees it."""
    art = page[page.index("lp-hero-art"):page.index("lp-section")]

    assert "example" in art.lower()


def test_the_caption_does_not_describe_events_that_did_not_happen(page):
    """"One source reached 4.6 sigma and was slowed" is a sentence about a
    thing that occurred. Nothing occurred."""
    lowered = page.lower()

    assert "was slowed" not in lowered
    assert "was blocked" not in lowered


def test_the_shape_is_still_shown(page):
    """Removing it would be the easy answer and the wrong one: showing a
    reader what these units look like is the page's actual job."""
    assert "lp-hero-art" in page
    assert "<svg" in page[page.index("lp-hero-art"):]


def test_no_figure_on_the_page_is_presented_as_a_customer_measurement(page):
    """The product has never published a customer's numbers, and must not
    start on the page whose whole argument is that it measures yours rather
    than somebody else's."""
    lowered = page.lower()

    assert "this site's own normal" not in lowered
    assert "24 hours of one site" not in lowered


def test_the_example_is_named_as_such_in_the_accessible_description_too(page):
    """A screen reader user gets the alt text and not the visible marker, so
    the honesty has to be in both or it is in neither."""
    art = page[page.index("lp-hero-art"):page.index("lp-section")]
    # The chart is an <svg role="img">, so its accessible name is the
    # <title>, not an aria-label.
    title = art[art.index("<title>"):art.index("</desc>")]

    assert "example" in title.lower()


# --- the two properties a rewrite would quietly drop ------------------------


def test_the_page_still_says_what_it_cannot_do(page):
    """A rewrite that drops this section turns the page into every other
    security vendor's page. It is the only thing on it a competitor would
    not print."""
    assert "doesn’t do yet" in page or "does not do yet" in page


def test_the_limits_section_is_specific_rather_than_a_gesture(page):
    """"We are always improving" is not this section. It names things the
    product does not have."""
    at = page.lower().index("do yet")
    section = page[at:at + 2500].lower()

    assert section.count("no ") + section.count("not ") >= 3


def test_the_landing_route_takes_no_resource_dependency():
    """Not a preference. Reading no DynamoDB is what lets this page sit
    behind a cache behaviour and cost nothing, and it is one careless
    Depends away from being false."""
    from services.backend.ui import public

    assert "get_dynamo_resource" not in inspect.getsource(public.landing)


def test_the_landing_page_reads_no_dynamodb(monkeypatch):
    """The mechanism above, checked through the door rather than at it: any
    table read at all fails this, however it got there."""
    from services.backend.core import tables

    def explode(*a, **kw):
        raise AssertionError("the landing page read DynamoDB")

    monkeypatch.setattr(tables._SimpleTable, "get", explode, raising=False)
    monkeypatch.setattr(tables._SimpleTable, "_query_all_pages", explode,
                        raising=False)

    app.dependency_overrides.pop(get_dynamo_resource, None)
    response = TestClient(app, base_url="https://testserver").get("/")

    assert response.status_code == 200
