"""Every asset a page asks for is an asset the server will hand over.

`static_files.py` serves from an explicit allow-list rather than a mounted
directory, and that is the right call: the path comes from a dict, so
traversal is not a class of bug that can happen here. It has one cost, and
this file is the payment.

Adding a `<script src>` to a template is one edit. Adding the file to the
allow-list is a second, in another file, and nothing connected them. Two
scripts shipped this way in a single afternoon - the command palette and the
bulk selection bar - and the failure is silent in the worst way: the page
renders, the markup is all there, the checkboxes are drawn, and nothing
works, because the browser got a 404 for the file that would have made them
work. No test that reads HTML can see it.

The reverse direction matters too, less urgently. An entry for a file that
no template references is either a deleted feature that left its stylesheet
behind or a typo that is about to become the first kind of bug.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.backend.main import app
from services.backend.ui.static_files import _ASSETS, _STATIC_DIR

_TEMPLATES = Path(__file__).resolve().parents[1] / "ui" / "templates"
_REFERENCE = re.compile(r"/ui/static/([A-Za-z0-9._-]+)")


def _referenced() -> set[str]:
    names = set()
    for template in _TEMPLATES.rglob("*.html"):
        names.update(_REFERENCE.findall(template.read_text(encoding="utf-8")))
    return names


def test_every_asset_a_template_asks_for_is_served():
    missing = _referenced() - set(_ASSETS)
    assert not missing, (
        f"{sorted(missing)} are referenced by a template and are not in the "
        f"allow-list, so the browser gets a 404 and the feature is silently "
        f"dead on a page that otherwise renders perfectly")


def test_every_served_asset_exists_on_disk():
    """A wrong name in the allow-list is a 500 at request time rather than a
    404, because FileResponse opens the path it was handed."""
    for name in _ASSETS:
        assert (_STATIC_DIR / name).is_file(), f"{name} is served but not shipped"


def test_no_asset_is_served_that_nothing_uses():
    """Either a removed feature left its file behind, or the name is a typo
    about to become the other kind of bug."""
    orphans = set(_ASSETS) - _referenced()
    assert not orphans, f"{sorted(orphans)} are served and referenced nowhere"


@pytest.mark.parametrize("name", sorted(_ASSETS))
def test_each_asset_is_actually_reachable(name):
    """The allow-list and the route agree, over HTTP, for each file."""
    r = TestClient(app, base_url="https://testserver").get(f"/ui/static/{name}")
    assert r.status_code == 200, name
    assert r.content


def test_an_unknown_name_is_refused():
    r = TestClient(app, base_url="https://testserver").get("/ui/static/../main.py")
    assert r.status_code == 404
