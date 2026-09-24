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

    css = (_STATIC / "app.css").read_text(encoding="utf-8")
    assert "repeat(6, 1fr)" in css


def test_both_layers_share_one_grid_cell_with_no_inline_padding():
    """Sigma s lands at s/6 x 100% in each layer. Padding on either one
    breaks that mapping, and nothing would report it."""
    css = (_STATIC / "app.css").read_text(encoding="utf-8")

    assert "grid-area: 1 / 1" in css
    assert "padding-inline: 0" in css


def test_the_enumerated_geometry_rules_are_gone():
    """223 rules and about 9KB, replaced by nothing. If these come back, the
    two-layer split has been abandoned."""
    css = (_STATIC / "app.css").read_text(encoding="utf-8")

    assert css.count("data-flex=") == 0
    assert css.count("data-at=") == 0


def test_the_stylesheet_budget_did_not_grow():
    """The cap is 80KB across three sheets, and the project sat at 80,006
    bytes when this task began. Adding the axis must not push it up."""
    total = sum(p.stat().st_size for p in _STATIC.glob("*.css"))

    assert total < 80_006, f"stylesheets grew to {total} bytes"


def test_nothing_uppercases_a_band_label():
    """CSS uppercases Greek: "4 sigma slowed" reached an incident screen as
    "4 Sigma SLOWED", which is summation, in a product that sells standard
    deviations."""
    css = re.sub(r"/\*.*?\*/", "",
                 (_STATIC / "app.css").read_text(encoding="utf-8"), flags=re.S)
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
