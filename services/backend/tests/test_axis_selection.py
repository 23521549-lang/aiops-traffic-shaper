"""Picking a source moves the picture, not just the list.

The axis is the primary object on this screen. An axis that renders
identically whether or not a source is selected turns the detail pane into a
second page that happens to sit beside it, and the operator loses the one
thing this product has that a rule engine does not: the selected decision's
position on the customer's own scale.
"""
from services.backend.ui.charts import build_axis, source_marks


def _rows():
    return [{"ip": "10.0.0.1", "z": -4.5}, {"ip": "10.0.0.2", "z": -6.1}]


def test_no_selection_lights_nothing():
    assert not any(m.selected for m in source_marks(_rows()))


def test_the_selected_source_is_the_only_one_lit():
    marks = source_marks(_rows(), selected_ip="10.0.0.2")

    assert [m.ip for m in marks if m.selected] == ["10.0.0.2"]


def test_a_selection_that_is_not_on_the_axis_lights_nothing():
    """A source whose z is None is dropped from the axis entirely. Selecting
    it must not light a neighbour that happens to be nearby."""
    rows = [{"ip": "10.0.0.1", "z": -4.5}, {"ip": "10.0.0.9", "z": None}]

    assert not any(m.selected for m in source_marks(rows, selected_ip="10.0.0.9"))


def test_selection_rides_through_build_axis():
    axis = build_axis(4.0, 5.0, {}, _rows(), selected_ip="10.0.0.1")

    assert [m.ip for m in axis.marks if m.selected] == ["10.0.0.1"]


def test_the_lit_mark_keeps_its_tier_class():
    """Selection is an extra state, not a replacement for severity. A
    selected blocked source that stops rendering as blocked is a lie about
    what was done to it."""
    marks = source_marks(_rows(), selected_ip="10.0.0.2")
    lit = next(m for m in marks if m.selected)

    assert "c-mark" in lit.css


def test_an_empty_selection_is_not_a_wildcard():
    """`?ip=` with nothing after it reaches the route as an empty string. It
    must light nothing rather than every source whose ip is also falsy."""
    rows = [{"ip": "", "z": -4.5}]

    assert not any(m.selected for m in source_marks(rows, selected_ip=""))
