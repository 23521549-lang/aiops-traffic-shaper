"""Chart geometry, tested as numbers rather than as angle brackets.

The whole reason charts are possible here is a CSP detail: `style=` is
blocked but SVG presentation attributes are not. So all the interesting
logic is arithmetic that produces attribute values, and arithmetic is
testable. `_charts.html` turns these objects into markup.
"""
import pytest

from services.backend.ui.charts import (
    SIGMA_CEILING,
    TIER1_SIGMA,
    TIER2_SIGMA,
    deviation_chart,
    downsample,
    enforcement_stack,
    sigma_strip,
    sparkline,
)


def _pts(*sigmas):
    return [(1_790_000_000 + i * 1800, s) for i, s in enumerate(sigmas)]


# --- the deviation chart -------------------------------------------------

def test_a_taller_bar_means_more_unusual():
    """|z| is plotted, not z. Asking a reader to interpret a downward axis
    where "more negative is worse" is an inversion with no upside."""
    chart = deviation_chart(_pts(-2.0, -5.5))
    shallow, deep = chart.bars
    assert deep.height > shallow.height


def test_each_bar_takes_its_tier_from_where_its_top_lands():
    chart = deviation_chart(_pts(-1.0, -4.2, -5.6))
    assert chart.bars[0].css.endswith("normal")
    assert chart.bars[1].css.endswith("limited")
    assert chart.bars[2].css.endswith("blocked")


def test_the_tier_boundaries_come_from_the_model_not_a_copy():
    """A recalibration must not leave the chart drawing last quarter's
    lines. ADR-006 moved these once already."""
    from services.backend.ml.model import TIER1_Z, TIER2_Z
    assert TIER1_SIGMA == abs(TIER1_Z)
    assert TIER2_SIGMA == abs(TIER2_Z)

    chart = deviation_chart(_pts(-(TIER1_SIGMA - 0.01), -TIER1_SIGMA))
    assert chart.bars[0].css.endswith("normal")
    assert chart.bars[1].css.endswith("limited")


def test_a_missing_bucket_is_not_drawn_as_a_calm_one():
    """None means "no data", which is not zero. A dead agent must not
    render as a healthy flat baseline — that is the exact failure the
    degraded empty state exists to prevent."""
    chart = deviation_chart(_pts(-4.5, None, -4.5))
    assert len(chart.bars) == 2


def test_one_extreme_outlier_does_not_flatten_everything_else():
    """Rescaling the axis around a 40σ event would compress every ordinary
    bar to a sliver. The outlier is capped and flagged instead."""
    chart = deviation_chart(_pts(-2.0, -40.0))
    assert chart.bars[1].clipped is True
    assert chart.bars[1].value == 40.0
    assert chart.bars[0].height > 0

    tallest = min(b.y for b in chart.bars)
    assert tallest >= chart.pad_top


def test_bars_stay_inside_the_plot_area():
    chart = deviation_chart(_pts(*[-(i % 7) for i in range(48)]))
    floor = chart.pad_top + chart.plot_height
    for bar in chart.bars:
        assert bar.y >= chart.pad_top - 0.01
        assert bar.y + bar.height <= floor + 0.01
        assert bar.x >= chart.pad_left - 0.01
        assert bar.x + bar.width <= chart.width - chart.pad_right + 0.01


def test_the_chrome_renders_even_with_no_data():
    """The empty state is the most-viewed screen in this product. Rendering
    the band, both threshold rules and both labels with no bars is how a
    customer with nothing blocked still learns what the product will do."""
    chart = deviation_chart([], empty_message="Nothing has crossed your threshold.")
    assert chart.bars == []
    assert len(chart.rules) == 2
    assert chart.band is not None
    assert chart.empty_message


def test_every_chart_carries_its_numbers_as_text():
    """An SVG a screen reader cannot read the values of is not accessible,
    and a pure-geometry chart would also silently defeat the
    tenant-isolation assertions that grep the response body."""
    chart = deviation_chart(_pts(-4.4, -5.2), label_for=lambda ts: f"t{ts}")
    assert len(chart.rows) == 2
    assert all(len(r) == 2 and r[1].endswith("σ") for r in chart.rows)


def test_time_anchors_are_placed_at_the_edges_and_middle():
    chart = deviation_chart(_pts(*[-1.0] * 9), label_for=lambda ts: str(ts))
    xs = [x for x, _ in chart.anchors]
    assert xs == sorted(xs)
    assert xs[0] == pytest.approx(chart.pad_left)
    assert xs[-1] == pytest.approx(chart.pad_left + chart.plot_width)


# --- the sigma strip -----------------------------------------------------

def test_the_strip_renders_with_nothing_active():
    strip = sigma_strip()
    assert len(strip.segments) == 3
    assert strip.marker is None


def test_the_marker_lands_where_the_sigma_says():
    strip = sigma_strip(-5.0)
    x, label = strip.marker
    assert label == "5.0σ"
    assert x == pytest.approx(strip.width * TIER2_SIGMA / SIGMA_CEILING)


def test_the_segments_tile_the_whole_strip_without_gaps():
    strip = sigma_strip()
    edges = [(seg[0], seg[0] + seg[1]) for seg in strip.segments]
    assert edges[0][0] == pytest.approx(0)
    assert edges[-1][1] == pytest.approx(strip.width)
    for (_, end), (start, _) in zip(edges, edges[1:]):
        assert end == pytest.approx(start)


def test_an_off_scale_marker_stays_on_the_strip():
    x, _ = sigma_strip(-99.0).marker
    assert x <= sigma_strip().width


# --- the enforcement stack -----------------------------------------------

def test_the_stack_is_proportional():
    bars = enforcement_stack(normal=50, slowed=25, blocked=25, width=100)
    assert [round(b.width) for b in bars] == [50, 25, 25]


def test_a_zero_segment_is_dropped_not_drawn_as_a_sliver():
    bars = enforcement_stack(normal=10, slowed=0, blocked=2)
    assert [b.label for b in bars] == ["normal", "blocked"]


def test_an_empty_stack_is_empty():
    assert enforcement_stack(0, 0, 0) == []


def test_the_stack_segments_are_contiguous():
    bars = enforcement_stack(3, 5, 7, width=300)
    for a, b in zip(bars, bars[1:]):
        assert a.x + a.width == pytest.approx(b.x)


# --- sparkline and downsampling ------------------------------------------

def test_a_single_point_is_not_a_trend():
    assert sparkline([5.0]) == ""
    assert sparkline([]) == ""


def test_a_flat_series_does_not_divide_by_zero():
    assert sparkline([3.0, 3.0, 3.0])


def test_downsampling_keeps_the_spike():
    """Averaging would erase exactly the thing the chart exists to show.
    An attack is a spike; the extreme in each window is what survives."""
    points = [(i, -1.0) for i in range(1000)]
    points[500] = (500, -6.5)

    out = downsample(points, limit=50)
    assert len(out) <= 50
    assert any(p[1] == -6.5 for p in out)


def test_downsampling_leaves_a_short_series_alone():
    points = _pts(-1.0, -2.0, -3.0)
    assert downsample(points, limit=240) is points


def test_downsampling_survives_missing_buckets():
    points = [(i, None if i % 3 else -2.0) for i in range(500)]
    assert downsample(points, limit=40)
