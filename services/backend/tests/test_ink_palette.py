"""The ink field is a dark surface and has to use the dark ramp.

`.on-ink` sets a near-black background and then inherited the light theme's
tier colours from `:root`, because nothing told it otherwise. On the landing
page that put pale mint and pale pink bands on near-black, and it made the
sigma marker invisible outright: the marker is `background: var(--text)`,
which on that page is #111823, drawn on #0d1b26. The one figure the section
exists to show - where this site currently sits on the scale - was not on
the screen at all.

The values are the ones already measured for `[data-theme="dark"]`. They are
repeated rather than shared because the dark block also redefines surfaces
and text that the ink field sets differently, so the two cannot simply be
merged. Repeated values drift, so this file asserts they have not.
"""
import re
from pathlib import Path

import pytest

_UI = Path(__file__).resolve().parents[1] / "ui" / "static"
_TOKENS = _UI / "tokens.css"

# `.on-ink` is FOUND, not named. It declares tokens but also sets background
# and colour on an element, which makes it a component carrying a theme
# rather than a theme, so it lives with whichever surface uses it - app.css
# once, landing.css after the sheets were split per surface. Ten assertions
# in this file went from guarding a real guarantee to erroring on a stale
# filename, silently, for exactly as long as nobody read the output.

# The ramp, and only the ramp. Surfaces and text differ between the two
# scopes on purpose.
# The ramp AND the three inks. `--t1..--t5` joined the list when the charts
# started reading the ramp directly instead of going through the tier
# fields: the ink field is a dark surface, so it needs the dark ramp, and
# nothing else would have noticed if it did not have one.
_SHARED = ("--t1", "--t2", "--t3", "--t4", "--t5",
           "--tier-normal", "--tier-limited", "--tier-blocked",
           "--tier-field", "--tier-blocked-edge")


def _block(selector: str) -> str:
    if selector.startswith("[data-theme"):
        sources = [_TOKENS]
    else:
        sources = sorted(_UI.glob("*.css"))
    for source in sources:
        css = re.sub(r"/\*.*?\*/", "", source.read_text(encoding="utf-8"), flags=re.S)
        start = css.find(selector)
        if start != -1:
            return css[start:css.index("}", start)]
    raise AssertionError(
        f"{selector} is in none of {[s.name for s in sources]}. If the ink "
        f"field was deleted, delete this file with it; if it was renamed, "
        f"rename it here. A block that cannot be found is not a block that "
        f"has been checked.")


def _tokens(block: str) -> dict[str, str]:
    return {name: value.strip()
            for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block)}


@pytest.fixture(scope="module")
def ink():
    return _tokens(_block(".on-ink {"))


@pytest.fixture(scope="module")
def dark():
    return _tokens(_block('[data-theme="dark"] {'))


@pytest.mark.parametrize("token", _SHARED)
def test_the_ink_field_uses_the_measured_dark_value(token, ink, dark):
    assert token in ink, (
        f"{token} is not set on .on-ink, so the ink field falls back to the "
        f"light ramp and draws a pale band on near-black")
    assert ink[token] == dark[token], (
        f"{token} is {ink[token]} on .on-ink and {dark[token]} in dark mode; "
        f"these were measured together and must not drift apart")


def test_text_on_ink_is_ink_text(ink):
    """The sigma marker and its label are `var(--text)`. On this field that
    was the light theme's near-black, on near-black."""
    assert ink.get("--text") == "var(--ink-fg)"
    assert ink.get("--text-muted") == "var(--ink-muted)"


def test_the_ink_field_does_not_redefine_a_surface(ink):
    """It has its own background. Redefining --surface here would leak into
    every card and table that happens to sit inside an ink section."""
    assert "--surface" not in ink
    assert "--bg" not in ink
