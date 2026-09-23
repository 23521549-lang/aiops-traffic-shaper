"""Nothing in a console table may be unreachable on a phone.

Measured, not guessed: at a 390px viewport the mitigation table is 519px
wide and the Allow button sits from x=463 to x=507. The console shell is
`height: 100vh; overflow: hidden`, so there is nothing to scroll and the
button is simply gone. Page-level overflow reads zero, which is why every
existing check was happy.

This is the second time the Allow button has left the screen. The first was
an `.sr-only` caption dragging the whole page sideways; this one is the
opposite shape, a table clipped by an ancestor, and the two share only the
symptom. What they do share is that no string assertion can see either.

So the invariant is structural and checkable here: a table in the console
lives inside a scroll container. The container is focusable and labelled,
because a region you can only reach by dragging is unreachable by keyboard,
which is the same bug wearing different clothes.
"""
import re
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parents[1] / "ui" / "templates"
_CSS = Path(__file__).resolve().parents[1] / "ui" / "static" / "console.css"

# Console tables only. The landing page has its own `.table-scroll`, which
# already does this and is tested by being on a page with no fixed height.
_CONSOLE_TABLE = re.compile(r'<table class="c-table"')
_SCROLLER = 'class="c-table-scroll"'


def _console_templates():
    for path in sorted(_TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        if _CONSOLE_TABLE.search(text):
            yield path, text


def test_there_are_console_tables_to_check():
    """A rename that makes the search find nothing would make every
    assertion below pass vacuously."""
    assert list(_console_templates())


@pytest.mark.parametrize("path,text", list(_console_templates()),
                         ids=lambda v: v.name if hasattr(v, "name") else "")
def test_every_console_table_sits_in_a_scroll_container(path, text):
    tables = len(_CONSOLE_TABLE.findall(text))
    scrollers = text.count(_SCROLLER)
    assert scrollers >= tables, (
        f"{path.name} has {tables} console table(s) and {scrollers} scroll "
        f"container(s); a table wider than the viewport is clipped by the "
        f"shell and its row actions cannot be reached at all")


def test_the_scroll_container_is_reachable_by_keyboard():
    """`overflow-x: auto` alone is a region only a pointer can enter."""
    for path, text in _console_templates():
        for block in text.split(_SCROLLER)[1:]:
            head = block[:200]
            assert 'tabindex="0"' in head, (
                f"{path.name}: a scroll container without tabindex can only "
                f"be scrolled by dragging")
            assert 'role="region"' in head, f"{path.name}: unlabelled scroll region"


def test_the_scroll_container_actually_scrolls():
    """The class has to exist and has to be the thing that scrolls, or the
    markup above is decoration."""
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    rule = re.search(r"\.c-table-scroll\s*\{([^}]*)\}", css)
    assert rule, ".c-table-scroll is used in templates and defined nowhere"
    assert "overflow-x" in rule.group(1)
