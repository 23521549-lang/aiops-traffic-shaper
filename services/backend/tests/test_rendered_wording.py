"""Defects found by rendering the product and reading it, not by testing it.

Every one of these passed every string assertion in the suite, because a
string assertion checks that a substring is present and none of these are
about absence. They are about what a person actually sees.

The sigma one is the worst of them, and the least visible from the code.
`.sigma-band` carries `text-transform: uppercase`, the labels read
"4σ slowed", and CSS uppercases Greek: the band on the main screen of a
product whose entire pitch is "we measure in standard deviations" rendered
"4Σ SLOWED". Σ is summation. σ is standard deviation. The landing page has a
section called "σ, in one screen" and the console was contradicting it in
the one place a customer looks during an incident.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, create_all_tables,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token
from services.backend.ui.presenters import humanise_age

_CSS = Path(__file__).resolve().parents[1] / "ui" / "static" / "console.css"


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="192.0.2.144", tier=1, score=-0.13, z=-4.6,
        reason="behavioral_anomaly", expires_at=0)
    return c


# --- sigma is not summation ----------------------------------------------

def test_no_rule_uppercases_text_that_can_contain_sigma():
    """The fix is not a smarter transform, it is not transforming. The
    labels are written in the case they are meant to be read in, and the
    only way that stays true is for no rule to override it."""
    # Comments first. The rule that caused this is now described, in prose,
    # inside the very block it was removed from, and a scan of the raw file
    # matches the explanation and reports the bug as still present.
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    blocks = re.findall(r"([^{}]+)\{([^}]*text-transform:\s*uppercase[^}]*)\}", css)
    offenders = [sel.strip() for sel, _ in blocks if "sigma" in sel or "c-seg" in sel]
    assert not offenders, (
        f"{offenders} uppercase their contents, and CSS uppercases Greek: a "
        f"sigma band label rendered 4Σ instead of 4σ")


def test_the_band_labels_say_sigma(client):
    page = client.get("/dashboard/ui").text
    assert "σ slowed" in page.lower() or "σ SLOWED" in page
    assert "Σ" not in page, "Σ is summation; this product measures in σ"


# --- counting in words ---------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (1, "1 second ago"),
    (5, "5 seconds ago"),
    (60, "1 minute ago"),
    (180, "3 minutes ago"),
    (3600, "1 hour ago"),
    (7200, "2 hours ago"),
    (86400, "1 day ago"),
    (172800, "2 days ago"),
])
def test_ages_agree_with_themselves(seconds, expected):
    """"1 minutes ago" and "1 seconds ago" shipped on the agents table, the
    page header and the fleet view at once, because two of the four branches
    handled the plural and two did not."""
    assert humanise_age(seconds) == expected


# --- the summary line ----------------------------------------------------

def test_the_page_summary_separates_its_clauses(client, dynamo_resource):
    """It rendered "last seen 2 minutes ago· 4 active". The separator lost
    its leading space to Jinja whitespace control."""
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01",
        registered_at="2026-09-01T00:00:00+00:00",
        last_seen_at="2026-09-23T00:00:00+00:00",
        agent_version="1.4.0", api_key_hash="h", status="active")

    page = client.get("/dashboard/ui").text
    assert "ago·" not in page
    assert "ago ·" in page


# --- no raw enums in front of a customer ---------------------------------

def test_the_reason_is_not_a_database_value(client):
    """The detail pane printed `behavioral_anomaly` under "Reason", in a
    pane whose whole job is explaining a decision to a person. Every other
    machine name in this product is mapped to words before it is shown."""
    page = client.get("/dashboard/ui?ip=192.0.2.144").text
    assert "behavioral_anomaly" not in page
