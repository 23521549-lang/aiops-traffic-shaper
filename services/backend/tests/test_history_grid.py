"""The same ruler, rotated.

The deviation chart this replaces plotted one number per hour: the worst sigma
seen. That answers "was there a spike" and cannot answer "where did my traffic
sit", which is the question the gate control is for. Turning the axis ninety
degrees and stacking one row per hour answers both from the same thirteen
bins, and lets the two gates be drawn literally cutting through the history
the operator is reading.
"""
from services.backend.schemas.history import HourlyPoint, MitigationEpisode
from services.backend.ui.charts import axis_x, history_grid


def _hours(n=3, start=3600):
    return [HourlyPoint(hour_start=start + i * 3600, requests=10, batches=1)
            for i in range(n)]


def _episode(hour, z):
    return MitigationEpisode(ip="10.0.0.7", hour_start=hour, first_ts=hour,
                             last_ts=hour, tier1_count=1, last_z=z)


def test_one_row_per_hour_in_the_window():
    assert len(history_grid(_hours(24), [], 4.0, 5.0).rows) == 24


def test_rows_run_oldest_at_the_top():
    """A chart is read downward like a log. Newest first would put the hour
    the customer opened the page for at the bottom of a 168-row grid."""
    grid = history_grid(_hours(3), [], 4.0, 5.0)

    assert [r.hour_start for r in grid.rows] == [3600, 7200, 10800]
    assert grid.rows[0].y < grid.rows[-1].y


def test_only_cells_with_a_count_are_emitted():
    """A requirement, not an optimisation. A dense 168-row grid emitting all
    thirteen bins per row is about 22KB of markup describing absence."""
    hours = _hours(3)
    hours[1].near = {"n300": 4}

    grid = history_grid(hours, [], 4.0, 5.0)

    assert [len(r.cells) for r in grid.rows] == [0, 1, 0]


def test_a_cell_sits_where_its_bin_sits_on_the_axis():
    """The same scale as the gate control and the main axis, so a gate at 4.0
    lines up with the 4.0 column without anything computing an offset."""
    hours = _hours(1)
    hours[0].near = {"n425": 2}

    cell = history_grid(hours, [], 4.0, 5.0).rows[0].cells[0]

    assert cell.x == axis_x(4.25)
    assert cell.sigma == 4.25


def test_the_gates_are_two_lines_and_not_per_row_decorations():
    grid = history_grid(_hours(3), [], 4.0, 5.0)

    assert grid.gate1_x == axis_x(4.0)
    assert grid.gate2_x == axis_x(5.0)


def test_an_hour_with_no_telemetry_is_marked_dead_not_quiet():
    """Principle 1.3. An empty row from a working agent and an empty row from
    a dead one look identical, and only one of them is evidence."""
    hours = _hours(2)
    hours[1].batches = 0

    assert [r.live for r in history_grid(hours, [], 4.0, 5.0).rows] == [True, False]


def test_an_identified_source_lands_on_the_row_for_its_own_hour():
    """The bins stop at the gate: a source past it is stored by address and
    never folded into the anonymous shape, so it has to be placed from the
    episodes or the grid ends at the gate."""
    grid = history_grid(_hours(3), [_episode(7200, -5.5)], 4.0, 5.0)

    assert [len(r.cells) for r in grid.rows] == [0, 1, 0]
    assert grid.rows[1].cells[0].identified is True
    assert grid.rows[1].cells[0].sigma == 5.5


def test_an_episode_with_no_z_is_dropped_rather_than_placed_at_zero():
    """z is None when the model had no usable spread. Drawing it at zero
    would put a blocked source inside the band labelled normal."""
    grid = history_grid(_hours(1), [_episode(3600, None)], 4.0, 5.0)

    assert grid.rows[0].cells == []


def test_an_episode_from_outside_the_window_is_dropped():
    """query_episodes reaches a whole hour past its bound so it does not drop
    the final hour. That means it can return an hour the series does not
    have, and a cell with no row to sit on must not invent one."""
    grid = history_grid(_hours(2), [_episode(999_999, -5.5)], 4.0, 5.0)

    assert all(r.cells == [] for r in grid.rows)


def test_intensity_is_bucketed_relative_to_the_busiest_cell():
    """Absolute counts cannot be drawn: one tenant sees four near-misses an
    hour and another sees four thousand."""
    hours = _hours(2)
    hours[0].near = {"n300": 100}
    hours[1].near = {"n300": 25}

    grid = history_grid(hours, [], 4.0, 5.0)

    assert grid.busiest == 100
    assert grid.rows[0].cells[0].level == 4
    assert grid.rows[1].cells[0].level == 1


def test_a_single_count_is_still_visible():
    """Level 0 renders as nothing, and one near-miss in an otherwise empty
    hour is exactly the reading this grid exists to show."""
    hours = _hours(1)
    hours[0].near = {"n300": 1}

    assert history_grid(hours, [], 4.0, 5.0).rows[0].cells[0].level >= 1


def test_an_empty_window_is_not_a_division_by_zero():
    grid = history_grid(_hours(3), [], 4.0, 5.0)

    assert grid.busiest == 0
    assert all(r.cells == [] for r in grid.rows)


def test_no_hours_at_all_is_an_empty_grid():
    grid = history_grid([], [], 4.0, 5.0)

    assert grid.rows == [] and grid.height == 0


def test_the_grid_gets_shorter_per_row_as_the_window_grows():
    """24 rows and 168 rows are the same picture at two scales. A fixed row
    height makes the 7-day view a very long scroll of the same thing."""
    day = history_grid(_hours(24), [], 4.0, 5.0)
    week = history_grid(_hours(168), [], 4.0, 5.0)

    assert week.row_height < day.row_height
    assert week.height <= day.height * 3


def test_a_row_holding_only_an_identified_source_is_not_called_dead():
    """An hour whose only record is a block it issued has plainly not lost
    its feed, whatever the batch counter says."""
    hours = _hours(1)
    hours[0].batches = 0

    grid = history_grid(hours, [_episode(3600, -5.5)], 4.0, 5.0)

    assert grid.rows[0].live is True


def test_a_source_past_the_ceiling_is_drawn_at_the_edge_not_off_it():
    grid = history_grid(_hours(1), [_episode(3600, -42.0)], 4.0, 5.0)

    assert grid.rows[0].cells[0].x == axis_x(6.0)
    assert grid.rows[0].cells[0].sigma == 42.0     # the reading is not clipped


def test_every_cell_stays_inside_the_drawing_area():
    hours = _hours(4)
    hours[0].near = {"n300": 5}
    hours[2].near = {"n600": 2}

    grid = history_grid(hours, [_episode(hours[1].hour_start, -5.5)], 4.0, 5.0)

    assert all(0 <= c.x <= grid.width for r in grid.rows for c in r.cells)
    assert all(0 <= r.y <= grid.height - grid.row_height for r in grid.rows)


def test_the_bins_survive_the_read_path(dynamo_resource):
    """record_traffic has written the thirteen bins onto the hourly row since
    Phase 0 and list_series dropped every one of them, exactly as list_history
    dropped last_features. A geometry test passes either way, because it is
    handed a model somebody constructed by hand."""
    from services.backend.api.routes.dashboard import list_series
    from services.backend.core.tables import TenantHistoryTable, create_all_tables

    create_all_tables(dynamo_resource)
    now = 1_700_000_000
    hour = TenantHistoryTable.hour_of(now)
    TenantHistoryTable(dynamo_resource).record_traffic(
        "t-1", hour, requests=10, bins={"n300": 7, "n475": 2})

    points = list_series(since=now - 3600, until=now + 3600,
                         tenant_id="t-1", resource=dynamo_resource)
    mine = next(p for p in points if p.hour_start == hour)

    assert mine.near == {"n300": 7, "n475": 2}


def test_an_hour_with_no_near_misses_carries_an_empty_dict_not_a_none(dynamo_resource):
    """query_series zero-fills hours DynamoDB has no row for. Those have no
    bins at all, and the grid must read them without a guard at every use."""
    from services.backend.api.routes.dashboard import list_series
    from services.backend.core.tables import create_all_tables

    create_all_tables(dynamo_resource)
    now = 1_700_000_000

    points = list_series(since=now - 7200, until=now,
                         tenant_id="t-1", resource=dynamo_resource)

    assert points and all(p.near == {} for p in points)
