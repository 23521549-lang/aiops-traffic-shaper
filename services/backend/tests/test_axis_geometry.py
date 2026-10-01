"""The axis is arithmetic, and the arithmetic is the design.

Everything with an arbitrary position goes in SVG, where CSP does not reach
and precision is free. Everything with TEXT stays in HTML, positioned at
integer sigma so it needs no computed position at all. That split is what
lets the 223 enumerated CSS rules go without a rule replacing them, and it
only works if the two layers agree on where sigma s lands - which is what
these tests pin.
"""
import pytest

from services.backend.ui.charts import (
    AXIS_UNITS_PER_SIGMA, SIGMA_CEILING, axis_bands, axis_x, build_axis,
    density_bars, source_marks,
)


def test_sigma_maps_to_a_hundred_units_each():
    """viewBox 0..600 for 0..6 sigma. A round number so a reader checking
    the markup by eye can do the division."""
    assert axis_x(0) == 0
    assert axis_x(1) == AXIS_UNITS_PER_SIGMA
    assert axis_x(4.5) == 450


def test_the_ceiling_clamps_rather_than_rescaling():
    """One source at 40 sigma must not flatten every other source into the
    first two pixels. SIGMA_CEILING is already how the deviation chart
    handles this."""
    assert axis_x(SIGMA_CEILING) == SIGMA_CEILING * AXIS_UNITS_PER_SIGMA
    assert axis_x(40) == axis_x(SIGMA_CEILING)


def test_a_negative_z_is_placed_by_its_magnitude():
    """z is negative by convention. The axis runs 0 to 6 and a raw -6.4
    would land off the left edge, silently."""
    assert axis_x(-6.4) == axis_x(6.4)


def test_the_bands_tile_the_axis_with_no_gap_and_no_overlap():
    bands = axis_bands(4.0, 5.0)

    assert len(bands) == 3
    assert bands[0].x == 0
    edges = [(b.x, b.x + b.width) for b in bands]
    for (_, end), (start, _) in zip(edges, edges[1:]):
        assert end == pytest.approx(start)
    assert edges[-1][1] == pytest.approx(SIGMA_CEILING * AXIS_UNITS_PER_SIGMA)


def test_the_bands_move_when_the_tenant_moves_their_gate():
    """The whole point of Phase 0's per-tenant threshold. A band system
    drawn from module constants would show every tenant someone else's
    gate."""
    default = axis_bands(4.0, 5.0)
    theirs = axis_bands(3.5, 4.5)

    assert theirs[0].width < default[0].width
    assert theirs[1].x == axis_x(3.5)


def test_a_band_label_never_relies_on_being_uppercased_by_css():
    """CSS uppercases Greek, and this product shipped "4Σ SLOWED" - which is
    summation - onto an incident screen. The labels are written in the case
    they are read in."""
    for band in axis_bands(4.0, 5.0):
        assert "Σ" not in band.label
        assert "Σ" not in band.short


def test_density_bars_come_from_the_recorded_bins():
    bars = density_bars({"n300": 40, "n325": 10})

    by_sigma = {b.sigma: b for b in bars}
    assert by_sigma[3.0].count == 40
    assert by_sigma[3.25].count == 10


def test_an_absent_bin_is_drawn_as_zero_not_skipped():
    """ADD only writes the bins a batch touched, so an untouched bin comes
    back ABSENT rather than 0. A chart that skips it silently redraws the
    axis with the wrong shape - the same zero-fill query_series(fill=True)
    already does for empty hours."""
    bars = density_bars({"n400": 5})

    assert len(bars) == 13
    assert all(b.count == 0 for b in bars if b.sigma != 4.0)


def test_the_tallest_bar_fills_the_band_and_the_rest_are_relative():
    """Absolute counts cannot be drawn - one tenant has four sources an hour
    and another has four thousand. The shape is the information."""
    bars = {b.sigma: b for b in density_bars({"n300": 100, "n350": 25})}

    assert bars[3.0].height == pytest.approx(1.0)
    assert bars[3.5].height == pytest.approx(0.25)


def test_no_bars_at_all_is_not_a_division_by_zero():
    assert all(b.height == 0 for b in density_bars({}))


def test_a_source_becomes_a_mark_at_its_own_position():
    marks = source_marks([{"ip": "203.0.113.7", "z": -6.4, "tier": 2}])

    assert marks[0].ip == "203.0.113.7"
    assert marks[0].x == axis_x(6.4)


def test_a_source_with_no_measurable_z_is_not_placed_at_zero():
    """z is None when the model had no usable spread. Drawing it at 0 would
    put a blocked source inside the band labelled "your normal traffic"."""
    assert source_marks([{"ip": "203.0.113.7", "z": None, "tier": 2}]) == []


def test_a_mark_carries_a_tier_class_so_colour_is_not_the_only_channel():
    """The tier ramp, matched to where the mark sits, so the same fact is
    carried by position and by hue rather than by hue alone."""
    blocked = source_marks([{"ip": "a", "z": -6.0, "tier": 2}])[0]
    normal = source_marks([{"ip": "b", "z": -1.0, "tier": 0}])[0]

    assert blocked.css != normal.css
    assert "c-mark" in blocked.css


def test_the_axis_carries_both_gates_where_the_tenant_put_them():
    axis = build_axis(tier1_sigma=3.5, tier2_sigma=4.5, bins={}, rows=[])

    assert axis.gate1_x == axis_x(3.5)
    assert axis.gate2_x == axis_x(4.5)


def test_the_ticks_are_integers_so_they_need_no_computed_position():
    """This is the half of the design that deletes the enumerated CSS. A
    tick at 4.37 sigma would need a rule per value; a tick at 4 is a grid
    column."""
    axis = build_axis(4.0, 5.0, bins={}, rows=[])

    assert [value for value, _ in axis.ticks] == [0, 1, 2, 3, 4, 5, 6]


def test_the_axis_is_constant_in_the_number_of_sources_below_the_gate():
    """The whole reason density exists. A tenant with ten thousand measured
    sources must cost the same bytes as one with forty."""
    small = build_axis(4.0, 5.0, bins={"n300": 40}, rows=[])
    huge = build_axis(4.0, 5.0, bins={"n300": 10_000}, rows=[])

    assert len(small.density) == len(huge.density)
    assert len(small.marks) == len(huge.marks) == 0
