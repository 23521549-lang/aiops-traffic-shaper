"""A chart drawn on two pages must be styled on both.

`_charts.html` is shared: the landing page and the console both call
`sigma_strip`. Only the console loads `console.css`, and every rule the
current macro needs - `.sigma-bands`, `.sigma-band`, `.c-seg--normal` and the
rest - lived there. The landing page has been rendering the band as three
unstyled lines of text ever since the console rebuild changed the markup,
under a heading that says "σ, in one screen", which is the one thing on that
page that has to look like an instrument.

`app.css` still carried the rules for the markup the macro used to emit
(`.sigma-scale`, `.sigma-normal`), matching nothing, which is why nothing
looked obviously broken in the stylesheet: the names were all present, just
not the ones in use.

So the rule is: a class emitted by a shared macro is defined in the sheet
both surfaces load. The console may add to it; it may not be the only place
the base appearance exists.
"""
import re
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parents[1] / "ui" / "templates"
_STATIC = Path(__file__).resolve().parents[1] / "ui" / "static"
_SHARED_MACROS = _TEMPLATES / "_charts.html"

# Templates that use the macros and do NOT load console.css.
_PUBLIC = ("landing.html", "login.html", "error.html")

_CLASS_ATTR = re.compile(r'class="([^"{}]*)"')


def _classes_emitted() -> set[str]:
    """Static class names in the shared macro file.

    Only literal ones: a class assembled from a Jinja expression is data the
    caller supplies, and `charts.py` supplies `c-seg c-seg--normal` and
    friends, which are checked below by name.
    """
    text = _SHARED_MACROS.read_text(encoding="utf-8")
    names = set()
    for group in _CLASS_ATTR.findall(text):
        names.update(n for n in group.split() if n and not n.startswith("{"))
    return names


def _defined_in(sheet: str) -> set[str]:
    css = re.sub(r"/\*.*?\*/", "", (_STATIC / sheet).read_text(encoding="utf-8"),
                 flags=re.S)
    return set(re.findall(r"\.([A-Za-z][\w-]*)", css))


def test_the_macro_file_still_emits_classes():
    assert _classes_emitted(), "the class scan found nothing; the pattern moved"


@pytest.mark.parametrize("name", sorted(_classes_emitted()))
def test_no_shared_chart_class_is_styled_only_in_the_console_sheet(name):
    """Not "every class must have a rule". A class may be an identifier and
    carry no appearance at all - `c-deviation` names which chart this is and
    two tests use it to assert the page shows the product's own chart. The
    defect is narrower and sharper: appearance that exists ONLY on the sheet
    the public pages never load."""
    if name not in _defined_in("console.css"):
        return  # no appearance anywhere, or app.css only: both are fine
    assert name in _defined_in("app.css"), (
        f".{name} is emitted by a shared chart macro and styled only in "
        f"console.css, so it renders unstyled on the public pages")


# `c-seg` itself is a BEM base with no appearance of its own; the variants
# are what carry colour, and they are what must exist on both surfaces.
@pytest.mark.parametrize("name", ["c-seg--normal", "c-seg--limited",
                                  "c-seg--blocked"])
def test_the_band_colours_charts_py_names_are_in_that_sheet_too(name):
    """These come out of `charts.py`, not the template, so the scan above
    cannot see them."""
    assert name in _defined_in("app.css"), (
        f".{name} is produced by charts.py and must be styled wherever a "
        f"chart is drawn, not only in the console")


def test_the_public_pages_do_not_load_the_console_sheet():
    """The premise of everything above. If this ever changes, these tests
    stop being about anything."""
    for name in _PUBLIC:
        path = _TEMPLATES / name
        if path.exists():
            assert "console.css" not in path.read_text(encoding="utf-8")
