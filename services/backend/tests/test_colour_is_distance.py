"""On the axis, colour is the distance.

The axis painted three flat bands, which says a source at 3.9 sigma and one at
4.1 sigma are opposite kinds of thing. They are nearly the same thing. What
changes between them is not the traffic, it is the decision the customer has
asked the product to make, and a decision is a line laid over a scale rather
than a property of the scale.

A continuous ramp says the true thing, and it does a second job for free: a
reader learns where the trouble is without reading a legend, which is the
whole of "do not make the viewer work".

Two ways this could go wrong, both pinned below. A gradient is a place for a
second palette to take root inside an SVG where nobody looks at it. And a
gradient is colour, so the words have to stay: WCAG 1.4.1 is not satisfied by
a prettier colour.
"""
import re
from pathlib import Path

_UI = Path(__file__).resolve().parents[1] / "ui"
_AXIS = (_UI / "templates" / "shared" / "_axis.html").read_text(encoding="utf-8")
# `_grid.html` is gone. The rotated axis it drew was 168 cells answering
# "did anything happen", and the approved history screen replaced it with a
# timeline: a few marks in the right places, each one pressable. The rule
# this file holds did not change - colour is a continuous ramp and the
# words stay - it now has one chart to hold it on instead of two.
_CSS = (_UI / "static" / "app.css").read_text(encoding="utf-8")


def test_the_axis_paints_a_continuous_scale():
    """Three flat fills assert a discontinuity the traffic does not have."""
    assert "linearGradient" in _AXIS


def test_the_gradient_stops_are_tier_tokens_and_not_new_colours():
    """A second palette living inside an SVG is a palette that drifts, in the
    one place no stylesheet test would ever look at it."""
    stops = re.findall(r"<stop[^>]*stop-color=\"([^\"]+)\"", _AXIS)

    assert stops, "no gradient stops found"
    for value in stops:
        assert value.startswith("var(--"), value


def test_the_gradient_is_built_from_the_tier_ramp():
    """Calm, near, acted on. The same three the rest of the product uses to
    mean exactly these things."""
    stops = " ".join(re.findall(r"<stop[^>]*stop-color=\"([^\"]+)\"", _AXIS))

    for token in ("--tier-normal", "--tier-limited", "--tier-blocked"):
        assert token in stops, token


def test_the_gates_are_still_drawn_as_lines_over_it():
    """The gate is where the product acts. It is not where the traffic
    changes, and drawing it as a fill boundary said it was."""
    assert "c-gate--1" in _AXIS
    assert "c-gate--2" in _AXIS


def test_a_source_mark_still_carries_its_tier_class():
    """Colour gained a second job here. It may not lose the first: a mark is
    what somebody reads off a screenshot pasted into a ticket."""
    assert "{{ m.css }}" in _AXIS


def test_the_bands_still_carry_their_words():
    """WCAG 1.4.1. A gradient is colour alone unless the labels stay, and
    they are what survives a greyscale print."""
    assert "c-axis-band-label" in _AXIS
    assert "b.label" in _AXIS


def test_the_timeline_carries_no_colour_of_its_own():
    """History used to draw a second ramp in a second chart. It now draws
    marks whose SIZE is the magnitude and whose fill is ink, so there is no
    second colour story to keep in step with the first."""
    hist = (_UI / "templates" / "dashboard_history.html").read_text(encoding="utf-8")

    assert "linearGradient" not in hist
    assert not re.search(r"(fill|stroke|stop-color)=\"#", hist)


def test_no_raw_hex_reaches_either_chart():
    """The rule the token system exists for, checked where it is easiest to
    break: SVG attributes are not CSS, so no stylesheet lint would catch a
    hex dropped in here."""
    for name, source in (("_axis.html", _AXIS),):
        assert not re.search(r"(fill|stroke|stop-color)=\"#", source), name
