"""The axis is two layers, and the split is load-bearing.

Arbitrary positions go in SVG, where CSP does not reach: `x`, `width` and
`points` are presentation attributes, not CSS, so precision is free and no
rule has to be enumerated. Text stays in HTML at integer sigma, where it
needs no computed position at all - and where it will not be stretched,
which is the reason the old sigma strip was flexbox in the first place.

Getting this wrong has a specific, shipped cost: 101 `data-flex` rules plus
101 `data-at` rules plus 21 `data-width` rules, about 9KB, rounding real
percentages to the nearest 5.
"""
import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parents[1] / "ui" / "static"
_SHARED = Path(__file__).resolve().parents[1] / "ui" / "templates" / "shared"


def _axis_template() -> str:
    return (_SHARED / "_axis.html").read_text(encoding="utf-8")


def test_no_inline_style_attribute_anywhere_in_the_axis():
    """`style-src 'self'` blocks the attribute, not only the block. An axis
    that needs one is an axis that does not render."""
    assert "style=" not in _axis_template()



def _axis_css() -> str:
    """The sheet that actually defines the axis, found rather than named.

    These rules lived in app.css and moved to landing.css when it turned out
    the landing page is the only surface that renders the macro, so the
    console was downloading them for nothing. Two of the assertions below
    failed at that moment, which is correct. Two others kept passing against
    a sheet that no longer contained a single axis rule, which is not: an
    assertion that its subject has moved away from is not a weaker test, it
    is a test of nothing at all. So the sheet is located by content, and it
    being missing everywhere is itself a failure.
    """
    found = []
    for name in ("app.css", "landing.css", "console.css"):
        text = (_STATIC / name).read_text(encoding="utf-8")
        if ".c-axis" in text:
            found.append((name, text))
    assert found, "no stylesheet defines .c-axis at all"
    assert len(found) == 1, (
        f"the axis is defined in more than one sheet ({[n for n, _ in found]}); "
        f"two copies drift and only one of them is the one being served")
    return found[0][1]


def test_the_svg_layer_carries_no_text():
    """`preserveAspectRatio="none"` stretches text horizontally with the
    box. That is why the old strip was flexbox, and why this layer contains
    only geometry."""
    svg = _axis_template().split("</svg>")[0]

    assert "<text" not in svg


def test_the_text_layer_is_a_grid_rather_than_positioned_boxes():
    """Labels sit at integer sigma, so they are grid items. This is what
    removes the enumeration entirely."""
    assert "c-axis-labels" in _axis_template()

    css = _axis_css()
    assert "repeat(6, 1fr)" in css


def test_both_layers_share_one_grid_cell_with_no_inline_padding():
    """Sigma s lands at s/6 x 100% in each layer. Padding on either one
    breaks that mapping, and nothing would report it."""
    css = _axis_css()

    assert "grid-area: 1 / 1" in css
    assert "padding-inline: 0" in css


def test_the_enumerated_geometry_rules_are_gone():
    """223 rules and about 9KB, replaced by nothing. If these come back, the
    two-layer split has been abandoned."""
    css = _axis_css()

    assert css.count("data-flex=") == 0
    assert css.count("data-at=") == 0


# The stylesheet budget used to be asserted here as well, against the sum of
# every sheet. It lives in test_static_assets.py now and is measured PER
# SURFACE, because the sheets became surface-specific and the sum is a figure
# no reader ever downloads. Two tests on one constraint is how the two drift.


def test_nothing_uppercases_a_band_label():
    """CSS uppercases Greek: "4 sigma slowed" reached an incident screen as
    "4 Sigma SLOWED", which is summation, in a product that sells standard
    deviations."""
    css = re.sub(r"/\*.*?\*/", "", _axis_css(), flags=re.S)
    blocks = re.findall(r"([^{}]+)\{([^}]*text-transform:\s*uppercase[^}]*)\}", css)

    assert not [sel for sel, _ in blocks if "axis" in sel or "c-seg" in sel]


def test_the_axis_carries_a_paired_table_that_is_a_summary():
    """Position on an axis is unreadable to a screen reader. A 13-row
    matrix of bin counts is not the answer - nobody wants thirteen numbers
    read out. The question the chart exists to answer is how much is past
    the gate."""
    template = _axis_template()

    assert "sr-only" in template
    assert "<table" in template
    assert template.count("<tr>") <= 4
