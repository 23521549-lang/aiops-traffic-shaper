"""What the console loses below 860px, and must not.

Measured at a 390px viewport, on the tenant's own Protection page:

  Protection       right edge  217
  History                      280
  Agents                       342
  Allowed IPs                  410   past the 390px screen
  Detection model              488   past the 390px screen

`.c-nav-group` already carries `overflow-x: auto`, so this looks fixed and
is not. It is a flex item, flex items default to `min-width: auto`, and an
item that refuses to shrink below its content never overflows - so the
scroll never engages and the last two destinations are simply gone. Allowed
IPs is where a customer lifts a block on a real visitor. It was unreachable
on a phone.

The second one is blunter. `.c-nav-foot` was display:none below 860px, and
Sign out lives in it, so a phone had no way to sign out at all.

Both are asserted against the stylesheet rather than a browser, because the
suite has no browser and these are one-line rules whose absence is the whole
bug. The comments say what was measured so the next person does not have to
re-measure to know why the rules are there.
"""
import re
from pathlib import Path

import pytest

_CSS = Path(__file__).resolve().parents[1] / "ui" / "static" / "console.css"


@pytest.fixture(scope="module")
def narrow() -> str:
    """The body of the narrow-width media query, comments stripped."""
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    start = css.index("@media (max-width: 860px)")
    depth = 0
    for i in range(css.index("{", start), len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start:i]
    raise AssertionError("the narrow media query is not closed")


def _rule(block: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*(?:,[^{]*)?\{([^}]*)\}", block)
    return match.group(1) if match else ""


def test_the_nav_can_scroll_to_its_last_item(narrow):
    """`overflow-x: auto` on a flex item does nothing until the item is
    allowed to be narrower than its contents."""
    rule = _rule(narrow, ".c-nav-group")
    assert "overflow-x" in rule, "the nav strip has to scroll on a phone"
    assert "min-width" in rule, (
        "a flex item defaults to min-width: auto, so it never shrinks below "
        "its content and the overflow-x above never engages; Allowed IPs and "
        "Detection model sat past the right edge of a 390px screen with no "
        "way to reach them")


def test_signing_out_is_possible_on_a_phone(narrow):
    """`.c-nav-foot` holds Sign out."""
    assert "display: none" not in _rule(narrow, ".c-nav-foot"), (
        "hiding the nav footer on a phone removes the only Sign out control "
        "in the product")


def test_the_nav_footer_is_hidden_selectively_not_wholesale(narrow):
    """The theme toggle and the palette button are conveniences and may go.
    The thing that ends a session may not."""
    assert ".c-nav-foot .theme-toggle" in narrow or ".theme-toggle" in narrow, (
        "if the footer is shown on a phone, its bulky parts need a rule of "
        "their own or the nav row has no space left for navigation")
