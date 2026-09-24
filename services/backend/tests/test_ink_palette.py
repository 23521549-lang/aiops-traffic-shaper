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
# `.on-ink` stayed in app.css: it declares tokens but also sets
# background and color on an element, which makes it a component
# carrying a theme rather than a theme. The dark ramp it is compared
# against moved to tokens.css with the rest of the ramps.
_CSS = _UI / "app.css"
_TOKENS = _UI / "tokens.css"

# The ramp, and only the ramp. Surfaces and text differ between the two
# scopes on purpose.
_SHARED = ("--tier-normal", "--tier-normal-bg", "--tier-limited",
           "--tier-limited-bg", "--tier-blocked", "--tier-blocked-bg",
           "--tier-blocked-edge", "--tier-limited-edge")


def _block(selector: str) -> str:
    source = _TOKENS if selector.startswith("[data-theme") else _CSS
    css = re.sub(r"/\*.*?\*/", "", source.read_text(encoding="utf-8"), flags=re.S)
    start = css.index(selector)
    return css[start:css.index("}", start)]


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
