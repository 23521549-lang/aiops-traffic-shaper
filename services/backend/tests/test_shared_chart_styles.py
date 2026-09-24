"""A macro both surfaces render may not be styled by the console sheet.

The old version of this file asked "is this class also in app.css?", which
permits a duplicate that then drifts. This asks the sharper question: does
`console.css` style anything a shared macro emits, WITHOUT a `.console`
ancestor? It may not.

The distinction is the whole rule. `.console .chart-caption { ... }` is the
console adding its own typography inside its own shell, and it is
structurally incapable of reaching the landing page, because `.console`
exists only in the console shell. `.chart-caption { ... }` in the same file
is a shared element being styled somewhere half the product cannot see, and
that defect has shipped: the sigma band rendered as three lines of unstyled
text on the public page, under a heading reading "sigma, in one screen".

The scan is a DIRECTORY, not a hand-maintained list of filenames. That is
the part that makes the rule survive someone adding a sixth macro file.
"""
import re
from pathlib import Path

import pytest

_UI = Path(__file__).resolve().parents[1] / "ui"
_SHARED = _UI / "templates" / "shared"
_STATIC = _UI / "static"

_CLASS_ATTR = re.compile(r'class="([^"{}]*)"')


def _shared_classes() -> set[str]:
    names = set()
    for template in sorted(_SHARED.rglob("*.html")):
        for group in _CLASS_ATTR.findall(template.read_text(encoding="utf-8")):
            names.update(n for n in group.split() if n and not n.startswith("{"))
    return names


def _unscoped_selectors(sheet: str) -> set[str]:
    """Classes this sheet styles WITHOUT a `.console` ancestor."""
    css = re.sub(r"/\*.*?\*/", "", (_STATIC / sheet).read_text(encoding="utf-8"),
                 flags=re.S)
    names = set()
    for block in re.findall(r"([^{}]+)\{[^}]*\}", css):
        for selector in block.split(","):
            selector = selector.strip()
            if not selector or ".console" in selector:
                continue
            names.update(re.findall(r"\.([A-Za-z][\w-]*)", selector))
    return names


def _selectors(sheet: str) -> set[str]:
    css = re.sub(r"/\*.*?\*/", "", (_STATIC / sheet).read_text(encoding="utf-8"),
                 flags=re.S)
    return set(re.findall(r"\.([A-Za-z][\w-]*)", css))


def test_there_is_a_shared_directory_to_scan():
    """If this ever finds nothing, every assertion below passes vacuously."""
    assert _SHARED.is_dir()
    assert list(_SHARED.rglob("*.html"))
    assert _shared_classes()


@pytest.mark.parametrize("name", sorted(_shared_classes()))
def test_the_console_sheet_never_styles_a_shared_class_unscoped(name):
    assert name not in _unscoped_selectors("console.css"), (
        f".{name} is emitted by a macro under templates/shared/ and styled "
        f"unscoped in console.css, which the public pages never load. Scope "
        f"it under `.console` if the console genuinely needs its own chrome, "
        f"or move it to app.css if every surface needs it")


@pytest.mark.parametrize("name", ["c-seg--normal", "c-seg--limited",
                                  "c-seg--blocked"])
def test_the_classes_charts_py_produces_obey_the_same_rule(name):
    """These come out of charts.py rather than the template, so the scan
    above cannot see them."""
    assert name not in _unscoped_selectors("console.css")
    assert name in _selectors("app.css")


def test_the_console_does_not_restate_a_rule_app_css_already_has():
    """`.console .chart { margin: 0 }` duplicated `.chart { margin: 0 }`
    exactly. A duplicate with the same value today is a duplicate with a
    different value after the first edit that touches only one of them."""
    console = (_STATIC / "console.css").read_text(encoding="utf-8")

    assert ".console .chart {" not in console


def test_tokens_are_in_their_own_sheet_and_loaded_first():
    """Both surfaces need the ramps; only the console needs the shell. Three
    sheets is the cap - a fourth is not allowed."""
    base = (_UI / "templates" / "base.html").read_text(encoding="utf-8")

    assert base.index("tokens.css") < base.index("app.css")
    assert len(list(_STATIC.glob("*.css"))) == 3
