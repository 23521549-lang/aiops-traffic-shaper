"""Thirteen answers at once, which is what beats a drag.

A slider shows one outcome at a time and hides the other twelve behind a
gesture. The gate has exactly thirteen legal positions, so the whole
response curve can be server-rendered and the operator reads it rather than
hunting for it.

The curve is CUMULATIVE, and that is the only reading of it that answers the
question being asked. "How many sources sit in the 3.5 bin" is trivia; "how
many would this gate catch" is the decision, and that is every source at or
past the line.
"""
import pytest

from services.backend.core.tables import TenantHistoryTable
from services.backend.ui.charts import gate_curve


def test_there_is_one_row_per_legal_gate_position():
    assert len(gate_curve({}, 4.0)) == len(TenantHistoryTable.NEAR_BINS)


def test_a_row_counts_every_source_at_or_past_its_line():
    """Cumulative, not per-bin. The question is what the gate would catch."""
    curve = {o.sigma: o for o in gate_curve({"n300": 5, "n325": 3, "n350": 2}, 4.0)}

    assert curve[3.0].count == 10
    assert curve[3.25].count == 5
    assert curve[3.5].count == 2
    assert curve[3.75].count == 0


def test_sources_already_past_the_gate_are_counted_at_every_line_below_it():
    """A source blocked at 6 sigma would also have been caught at 3. Leaving
    it out would tell an operator that lowering the gate catches fewer
    sources than raising it, which is backwards."""
    curve = {o.sigma: o for o in gate_curve({"n300": 5}, 4.0, blocked_total=7)}

    assert curve[3.0].count == 12
    assert curve[6.0].count == 7


def test_the_current_position_is_marked():
    marked = [o for o in gate_curve({}, 4.25) if o.current]

    assert len(marked) == 1
    assert marked[0].sigma == 4.25


def test_a_current_position_off_the_scale_marks_nothing_rather_than_guessing():
    """A tenant whose stored value predates the thirteen positions. Marking
    the nearest one would tell them their gate is somewhere it is not."""
    assert not any(o.current for o in gate_curve({}, 4.1))


def test_the_bar_widths_are_relative_to_the_widest_row():
    """Absolute counts cannot be drawn: one tenant sees four near-misses an
    hour and another sees four thousand."""
    curve = {o.sigma: o for o in gate_curve({"n300": 100}, 4.0)}

    assert curve[3.0].width == pytest.approx(1.0)
    assert curve[6.0].width == 0


def test_an_empty_curve_is_not_a_division_by_zero():
    assert all(o.width == 0 for o in gate_curve({}, 4.0))


def test_the_curve_never_rises_as_the_gate_rises():
    """A cumulative count can only fall. If it ever climbs, the arithmetic
    has been inverted and the control is advising the opposite of the
    truth."""
    counts = [o.count for o in gate_curve({"n300": 9, "n375": 4, "n500": 2}, 4.0)]

    assert counts == sorted(counts, reverse=True)


def test_the_lowest_positions_are_flagged_as_not_recommended():
    """ADR-006 measured 0.82% false positives at 3.5 sigma against 0.27% at
    4.0, and the rate climbs steeply below. The control may offer these and
    must not present them as ordinary."""
    curve = {o.sigma: o for o in gate_curve({}, 4.0)}

    assert curve[3.0].recommended is False
    assert curve[3.5].recommended is False
    assert curve[4.0].recommended is True
