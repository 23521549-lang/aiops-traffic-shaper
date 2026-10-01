"""Every colour pair in the stylesheet, measured, on every surface it lands on.

This test exists because of a specific mistake. `--border-interactive` was
introduced with the comment "3.18:1 on --surface, WCAG 1.4.11" — measured
once, against white, and declared passing. On `--bg` the same value is
2.97:1, below the 3:1 that 1.4.11 requires for a UI component boundary. A
`.btn` or an `input` sitting in a `.form-row` outside a card lands on `--bg`,
so the failing case was reachable on the shipped page.

Measuring by eye, or once, does not survive a redesign. This reads the real
token values out of `tokens.css` and computes the real ratios, so a token that
drifts fails here rather than in someone's browser.

Ratios use the WCAG 2.x relative-luminance formula. Thresholds:
  4.5:1  normal text
  3.0:1  large text (>=24px, or >=18.66px bold) and UI component boundaries
"""
import re
from pathlib import Path

import pytest

# The ramps moved to tokens.css when app.css was split, so that every
# surface loads them and only the console loads console.css.
# The ramps live in tokens.css; the component rules that carry them stayed
# in app.css. Two paths, because this file asks two different questions:
# "is this pair readable" is about tokens, and "does this component rely
# on colour alone" is about rules.
CSS = Path(__file__).resolve().parents[1] / "ui" / "static" / "tokens.css"
RULES = Path(__file__).resolve().parents[1] / "ui" / "static" / "app.css"


def _linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def ratio(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _tokens(scope: str) -> dict[str, str]:
    """Pull `--name: #hex;` declarations out of one selector block.

    Deliberately a parser over the real file rather than a copy of the
    values: a test that carries its own copy of the palette passes happily
    while the stylesheet says something else.
    """
    css = CSS.read_text(encoding="utf-8")
    start = css.index(scope)
    block = css[start:css.index("}", start)]
    return {m.group(1): m.group(2)
            for m in re.finditer(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", block)}


# `:root` is the LIGHT theme again, and there is no separate
# `[data-theme="light"]` block to parse: the default IS light, so a second
# copy of it would be a duplicate waiting to drift.
#
# The default moved twice. It went dark when the palette was three saturated
# hues that a dark field held apart, and came back to light when the palette
# became one hue in five steps, which separates on white. Both scopes are
# measured either way; only which one a reader lands on ever moved.
DEFAULT = LIGHT = _tokens(":root {")


def _pairs(t: dict[str, str]) -> list[tuple[str, str, str, float]]:
    """(label, foreground, background, required ratio)."""
    surfaces = [("surface", t["--surface"]), ("bg", t["--bg"])]
    if "--surface-sunken" in t:
        surfaces.append(("sunken", t["--surface-sunken"]))

    out: list[tuple[str, str, str, float]] = []
    for name in ("--text", "--text-muted", "--accent"):
        for label, bg in surfaces:
            out.append((f"{name} on {label}", t[name], bg, 4.5))

    # UI component boundaries: 1.4.11. This is the row that was wrong.
    for label, bg in surfaces:
        out.append((f"--border-interactive on {label}", t["--border-interactive"], bg, 3.0))
        out.append((f"--focus-ring on {label}", t["--focus-ring"], bg, 3.0))

    # Text on a filled control. Which foreground is correct depends on the
    # theme: light puts white on a deep teal, dark puts ink on a bright
    # cyan. Demanding "white" in both would have required 1.88:1.
    label = "#ffffff" if luminance(t["--accent"]) < 0.4 else t["--ink"]
    out.append(("button label on --accent", label, t["--accent"], 4.5))

    # Axis A: how far a source sits from this tenant's own normal. All three
    # sit on ONE field now. Each used to have its own darker tint, which read
    # well and measured 4.08:1 - a chip nobody could use. Magnitude moved to
    # the ink; the field stayed pale so the ink can be read on it.
    for tier in ("normal", "limited", "blocked"):
        out.append((f"--tier-{tier} on the field", t[f"--tier-{tier}"],
                    t["--tier-field"], 4.5))
        out.append((f"--tier-{tier} on surface", t[f"--tier-{tier}"], t["--surface"], 4.5))

    # Axis B: is the product itself healthy. These shipped unmeasured.
    for health in ("success", "warning", "danger"):
        if f"--{health}" in t:
            out.append((f"--{health} on its bg", t[f"--{health}"], t[f"--{health}-bg"], 4.5))

    return out


@pytest.mark.parametrize("label,fg,bg,need", _pairs(LIGHT), ids=lambda v: None)
def test_light_theme_pair_meets_wcag(label, fg, bg, need):
    got = ratio(fg, bg)
    assert got >= need, f"light {label}: {got:.2f}:1 < {need}:1  ({fg} on {bg})"


@pytest.mark.parametrize("label,fg,bg,need", _pairs(DEFAULT), ids=lambda v: None)
def test_the_default_theme_pair_meets_wcag(label, fg, bg, need):
    """What a reader gets before choosing anything. It used to be the light
    ramp; it is the dark one now, and the pairs are measured either way."""
    got = ratio(fg, bg)
    assert got >= need, f"default {label}: {got:.2f}:1 < {need}:1  ({fg} on {bg})"


def test_the_border_that_was_wrong_is_right_now():
    """Named explicitly so the regression is findable by the thing that
    caused it, not just by a generic parametrised id."""
    assert ratio(LIGHT["--border-interactive"], LIGHT["--bg"]) >= 3.0


def test_a_tier_is_never_conveyed_by_colour_alone():
    """WCAG 1.4.1, and the guarantee that actually matters.

    An earlier version of this test compared the three tier colours by
    contrast RATIO and demanded they differ. That is the wrong instrument:
    contrast ratio measures relative luminance, so it says green #0f7a4d and
    amber #9a4a06 are "too close" at 1.17:1 when they are plainly different
    hues. Two colours of similar lightness are perfectly distinguishable —
    unless the viewer has a colour vision deficiency, which is exactly why
    colour is not allowed to be the only channel in the first place.

    So assert the real thing: every tier badge carries a word.
    """
    # Not a grep over the templates: the class is interpolated there as
    # `{{ m.tier_css }}`, so the literal string never appears in the source
    # and a grep would pass or fail for reasons unrelated to the guarantee.
    from services.backend.ui.presenters import tier_css, tier_label

    css = RULES.read_text(encoding="utf-8")
    for tier in (1, 2):
        assert f".{tier_css(tier)}" in css, tier_css(tier)
        assert tier_label(tier).strip(), tier
    assert tier_label(1) != tier_label(2)


def test_the_brand_cyan_cannot_be_used_on_a_light_surface():
    """Load-bearing constraint, not an oversight. --brand-cyan is unusable
    on white by design, which is what stops it ever appearing as an
    interface colour: it can only live on an ink field or in dark mode, so
    it reads as brand and can never be mistaken for the tier ramp."""
    if "--brand-cyan" not in LIGHT:
        pytest.skip("brand cyan not defined yet")
    assert ratio(LIGHT["--brand-cyan"], LIGHT["--surface"]) < 3.0
    assert ratio(LIGHT["--brand-cyan"], LIGHT["--ink"]) >= 4.5


def test_the_ink_field_carries_its_text():
    if "--ink" not in LIGHT:
        pytest.skip("ink field not defined yet")
    assert ratio(LIGHT["--ink-fg"], LIGHT["--ink"]) >= 4.5
    assert ratio(LIGHT["--ink-muted"], LIGHT["--ink"]) >= 4.5


# --- dark ---------------------------------------------------------------

def _dark() -> dict[str, str]:
    try:
        return _tokens('[data-theme="dark"] {')
    except ValueError:
        return {}


DARK = _dark()


@pytest.mark.parametrize("label,fg,bg,need", _pairs(DARK) if DARK else [],
                         ids=lambda v: None)
def test_dark_theme_pair_meets_wcag(label, fg, bg, need):
    """The console is read at 3am. An unmeasured dark theme would be a
    regression against a light theme that has ratios in its comments."""
    got = ratio(fg, bg)
    assert got >= need, f"dark {label}: {got:.2f}:1 < {need}:1  ({fg} on {bg})"


@pytest.mark.skipif(not DARK, reason="dark theme not defined yet")
def test_dark_text_is_not_pure_white():
    """#ffffff on a near-black field halates. Backing off to ~15:1 is
    deliberate and still far above AA."""
    assert DARK["--text"].lower() != "#ffffff"
    assert ratio(DARK["--text"], DARK["--surface"]) >= 4.5


# --- layout guards -------------------------------------------------------

def test_scroll_containers_contain_their_absolutely_positioned_children():
    """`.sr-only` is absolutely positioned. With no positioned ancestor it
    anchors to the initial containing block rather than the scroll container
    it sits inside, so a hidden "Actions" label in a table header landed at
    x=450 on a 390px screen and dragged the whole page sideways with it.

    That carried the "Allow this IP" button off the right edge, which made
    the single most urgent action in the product unreachable on a phone.

    Found by walking the rendered box tree in a real browser. No assertion
    on a response body can see it, which is why the structural guard is
    pinned here instead."""
    css = RULES.read_text(encoding="utf-8")
    for selector in (".table-scroll", ".card"):
        block = css[css.index(selector + " {"):]
        block = block[:block.index("}")]
        assert "position: relative" in block, selector


def test_the_screen_reader_table_cannot_widen_the_page():
    """A <table> does not clip the way a block does: the sr-only tables that
    carry every chart's numbers were laying out at full min-content width.
    Two defences, because `clip` is deprecated and `clip-path` is the
    replacement, and both are cheap."""
    css = RULES.read_text(encoding="utf-8")
    block = css[css.index(".sr-only {"):]
    block = block[:block.index("}")]
    assert "clip-path: inset(50%)" in block
    assert "overflow: hidden" in block
    assert ".sr-only table { width: 1px; }" in css
