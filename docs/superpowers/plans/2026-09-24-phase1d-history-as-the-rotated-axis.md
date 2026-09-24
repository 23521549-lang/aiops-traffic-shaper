# Phase 1d — history as the rotated axis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the console's 240-point deviation chart with the same sigma
ruler rotated: one row per hour, position along the row is sigma, the two
gates are two vertical lines cutting through every row. Then make "decided"
and "in effect" two visibly different states wherever enforcement is shown,
and put the tier-2 gate behind the 7-day tab where it has evidence.

**Architecture:** The history grid is not a new chart type. It is `build_axis`
turned ninety degrees and stacked, reading the same thirteen bins per hour
that the gate curve reads in aggregate, from the same Query the history page
already makes. Only cells with a count are emitted, which is a requirement
rather than an optimisation: a dense 168-row grid emitting every empty cell is
roughly 22KB of markup for information that is not there.

**Tech Stack:** FastAPI, Jinja2, htmx 2.0.4 (vendored), server-rendered SVG,
DynamoDB single-table.

**Spec:** `docs/ui-rebuild/05-spec-thiet-ke.md` §3.4, §4.4, §5.2, §5.3, §1.5

## Global Constraints

- No em dash may appear in any rendered UI string.
- CSP is `default-src 'self'; script-src 'self'; style-src 'self'`. No inline
  `style=` attribute, no inline `<script>`. SVG presentation attributes are
  not CSS and are unaffected.
- Every mutation is a body-less POST with its values in the query string, sent
  by htmx so the CSRF token is inherited from the console wrapper's
  `hx-headers`.
- Total CSS across `services/backend/ui/static/*.css` stays under 80,000
  bytes. It is at 75,578 at the start of this phase, so this phase has roughly
  4,400 bytes. Enumerated rules are affordable only for bucketed values.
- `templates/shared/` is for macros BOTH surfaces render. A console-only macro
  belongs in `templates/`. `test_shared_chart_styles.py` enforces this.
- No new DynamoDB read on the scoring path, and no new Query on the console
  history page: everything this phase draws comes from reads already made.
- Never invent a reading. An hour with no telemetry is a hole, not a zero.
- Coverage gate: 80%. Run `ruff check` as a separate command from pytest.

---

## Task 1: The grid geometry

**Files:**
- Modify: `services/backend/ui/charts.py`
- Test: `services/backend/tests/test_history_grid.py` (create)

**Interfaces:**
- Consumes: `axis_x(sigma)`, `AXIS_UNITS_PER_SIGMA`, `SIGMA_CEILING`,
  `axis_bands`, `_tier_css` from `charts.py`; `TenantHistoryTable.NEAR_BINS`.
- Consumes: `HourlyPoint(hour_start, requests, batches)` and
  `MitigationEpisode(ip, hour_start, last_z, ...)`.
- Produces: `history_grid(series, episodes, tier1_sigma, tier2_sigma) -> Grid`
  where `Grid(width, height, row_height, rows: list[GridRow], bands,
  gate1_x, gate2_x, gate1_sigma, gate2_sigma, busiest)` and
  `GridRow(hour_start, y, live: bool, cells: list[GridCell])`,
  `GridCell(x, width, count, level: int, sigma: float, identified: bool)`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_history_grid.py`:

```python
"""The same ruler, rotated.

The deviation chart this replaces plotted one number per hour: the worst
sigma seen. That answers "was there a spike" and cannot answer "where did my
traffic sit", which is the question the gate control is for. Turning the axis
ninety degrees and stacking one row per hour answers both from the same
thirteen bins, and lets the two gates be drawn literally cutting through the
history the operator is reading.
"""
import pytest

from services.backend.schemas.history import HourlyPoint, MitigationEpisode
from services.backend.ui.charts import history_grid


def _hours(n=3, start=3600):
    return [HourlyPoint(hour_start=start + i * 3600, requests=10, batches=1)
            for i in range(n)]


def test_one_row_per_hour_in_the_window():
    grid = history_grid(_hours(24), [], 4.0, 5.0)

    assert len(grid.rows) == 24


def test_rows_run_oldest_at_the_top():
    """A chart is read downward like a log. Newest-first would put the hour
    the customer opened the page for at the bottom of a 168-row grid."""
    grid = history_grid(_hours(3), [], 4.0, 5.0)

    assert [r.hour_start for r in grid.rows] == [3600, 7200, 10800]
    assert grid.rows[0].y < grid.rows[-1].y


def test_only_cells_with_a_count_are_emitted():
    """A requirement, not an optimisation. A dense 168-row grid emitting all
    thirteen bins per row is about 22KB of markup describing absence."""
    hours = _hours(3)
    hours[1].n300 = 4

    grid = history_grid(hours, [], 4.0, 5.0)

    assert [len(r.cells) for r in grid.rows] == [0, 1, 0]


def test_a_cell_sits_where_its_bin_sits_on_the_axis():
    """Same scale as the gate control and the main axis, so a gate at 4.0
    lines up with the 4.0 column without anything computing an offset."""
    from services.backend.ui.charts import axis_x

    hours = _hours(1)
    hours[0].n425 = 2

    cell = history_grid(hours, [], 4.0, 5.0).rows[0].cells[0]

    assert cell.x == axis_x(4.25)
    assert cell.sigma == 4.25


def test_the_gates_are_two_lines_and_not_per_row_decorations():
    from services.backend.ui.charts import axis_x

    grid = history_grid(_hours(3), [], 4.0, 5.0)

    assert grid.gate1_x == axis_x(4.0)
    assert grid.gate2_x == axis_x(5.0)


def test_an_hour_with_no_telemetry_is_marked_dead_not_quiet():
    """Principle 1.3. An empty row from a working agent and an empty row
    from a dead one look identical, and only one of them is evidence."""
    hours = _hours(2)
    hours[1].batches = 0

    grid = history_grid(hours, [], 4.0, 5.0)

    assert [r.live for r in grid.rows] == [True, False]


def test_an_identified_source_lands_on_the_row_for_its_own_hour():
    """The bins stop at the gate: a source past it is stored by address and
    never folded into the anonymous shape, so it has to be placed from the
    episodes or the grid ends at the gate."""
    grid = history_grid(_hours(3), [MitigationEpisode(
        ip="10.0.0.7", hour_start=7200, first_ts=7200, last_ts=7200,
        tier1_count=1, last_z=-5.5)], 4.0, 5.0)

    assert [len(r.cells) for r in grid.rows] == [0, 1, 0]
    assert grid.rows[1].cells[0].identified is True
    assert grid.rows[1].cells[0].sigma == 5.5


def test_an_episode_with_no_z_is_dropped_rather_than_placed_at_zero():
    """z is None when the model had no usable spread. Drawing it at zero
    would put a blocked source inside the band labelled normal."""
    grid = history_grid(_hours(1), [MitigationEpisode(
        ip="10.0.0.7", hour_start=3600, first_ts=3600, last_ts=3600,
        tier1_count=1, last_z=None)], 4.0, 5.0)

    assert grid.rows[0].cells == []


def test_an_episode_from_outside_the_window_is_dropped(caplog):
    """query_episodes reaches a whole hour past the bound to avoid dropping
    the final hour. That means it can return an hour the series does not
    have, and a cell with no row to sit on must not invent one."""
    grid = history_grid(_hours(2), [MitigationEpisode(
        ip="10.0.0.7", hour_start=999_999, first_ts=999_999,
        last_ts=999_999, tier1_count=1, last_z=-5.5)], 4.0, 5.0)

    assert all(r.cells == [] for r in grid.rows)


def test_intensity_is_bucketed_relative_to_the_busiest_cell():
    """Absolute counts cannot be drawn: one tenant sees four near-misses an
    hour and another sees four thousand."""
    hours = _hours(2)
    hours[0].n300 = 100
    hours[1].n300 = 25

    grid = history_grid(hours, [], 4.0, 5.0)

    assert grid.busiest == 100
    assert grid.rows[0].cells[0].level == 4
    assert grid.rows[1].cells[0].level == 1


def test_a_single_count_is_still_visible():
    """Level 0 would render as nothing, and one near-miss in an otherwise
    empty hour is exactly the reading this grid exists to show."""
    hours = _hours(1)
    hours[0].n300 = 1

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
    height makes the 7-day view 1,680 pixels of scrolling."""
    day = history_grid(_hours(24), [], 4.0, 5.0)
    week = history_grid(_hours(168), [], 4.0, 5.0)

    assert week.row_height < day.row_height
    assert week.height <= day.height * 3
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_history_grid.py -q`
Expected: `ImportError: cannot import name 'history_grid'`

- [ ] **Step 3: Write it**

Append to `services/backend/ui/charts.py`:

```python
# --- history: the same axis, rotated and stacked ---------------------------

# Twenty-four rows read comfortably at 10 units each; a hundred and sixty
# eight at that height is 1,680 units of scrolling for the same picture. The
# grid keeps its total height roughly constant instead.
GRID_MAX_HEIGHT = 260.0
GRID_MAX_ROW_HEIGHT = 10.0
GRID_LEVELS = 4


@dataclass
class GridCell:
    x: float
    width: float
    count: int
    level: int
    sigma: float
    identified: bool


@dataclass
class GridRow:
    hour_start: int
    y: float
    live: bool
    cells: list[GridCell] = field(default_factory=list)


@dataclass
class Grid:
    width: float
    height: float
    row_height: float
    busiest: int
    rows: list[GridRow] = field(default_factory=list)
    bands: list[Band] = field(default_factory=list)
    gate1_x: float = 0.0
    gate2_x: float = 0.0
    gate1_sigma: float = 0.0
    gate2_sigma: float = 0.0
    ticks: list[tuple[int, str]] = field(default_factory=list)


def history_grid(series, episodes, tier1_sigma: float,
                 tier2_sigma: float) -> Grid:
    """One row per hour, sigma along the row, gates straight through.

    The deviation chart this replaces plotted one number per hour - the worst
    sigma seen - which answers "was there a spike" and cannot answer "where
    did my traffic sit". Both come out of the thirteen bins that the hourly
    row already carries, so this is a second reading of a Query the page
    already makes rather than a second Query.

    Only cells with a count are emitted. That is a requirement and not an
    optimisation: a dense 168-row grid emitting all thirteen bins per row is
    about 22KB of markup describing absence.
    """
    ordered = sorted(series, key=lambda p: p.hour_start)
    if not ordered:
        return Grid(width=SIGMA_CEILING * AXIS_UNITS_PER_SIGMA, height=0,
                    row_height=0, busiest=0)

    row_height = min(GRID_MAX_ROW_HEIGHT, GRID_MAX_HEIGHT / len(ordered))
    edges = TenantHistoryTable.NEAR_BINS
    step = (edges[1] - edges[0]) if len(edges) > 1 else 0.25
    cell_width = step * AXIS_UNITS_PER_SIGMA

    # Identified sources are not in the bins: a source past the gate is stored
    # by address and never folded into the anonymous shape, so without this
    # the grid would stop at the gate.
    by_hour: dict[int, list[MitigationEpisodeLike]] = {}
    for e in episodes:
        if e.last_z is None:
            continue
        by_hour.setdefault(int(e.hour_start), []).append(e)

    rows, busiest = [], 0
    for i, point in enumerate(ordered):
        hour = int(point.hour_start)
        cells = []
        for edge in edges:
            count = int(getattr(point, "n%d" % round(edge * 100), 0) or 0)
            if not count:
                continue
            busiest = max(busiest, count)
            cells.append(GridCell(x=axis_x(edge), width=cell_width,
                                  count=count, level=0, sigma=edge,
                                  identified=False))
        for e in by_hour.get(hour, []):
            magnitude = min(abs(e.last_z), SIGMA_CEILING)
            cells.append(GridCell(x=axis_x(magnitude), width=2.0, count=1,
                                  level=GRID_LEVELS, sigma=abs(e.last_z),
                                  identified=True))
        rows.append(GridRow(hour_start=hour, y=round(i * row_height, 2),
                            live=bool(point.batches) or bool(cells),
                            cells=cells))

    # Bucketed after the fact, because the busiest cell is not known until
    # every row has been read. Absolute counts cannot be drawn: one tenant
    # sees four near-misses an hour and another sees four thousand.
    for row in rows:
        for cell in row.cells:
            if cell.identified:
                continue
            share = cell.count / busiest if busiest else 0
            # Never zero. One near-miss in an otherwise empty hour is exactly
            # the reading this grid exists to show, and level 0 draws nothing.
            cell.level = max(1, math.ceil(share * GRID_LEVELS))

    return Grid(
        width=SIGMA_CEILING * AXIS_UNITS_PER_SIGMA,
        height=round(len(rows) * row_height, 2),
        row_height=round(row_height, 2),
        busiest=busiest,
        rows=rows,
        bands=axis_bands(tier1_sigma, tier2_sigma),
        gate1_x=axis_x(tier1_sigma),
        gate2_x=axis_x(tier2_sigma),
        gate1_sigma=tier1_sigma,
        gate2_sigma=tier2_sigma,
        ticks=[(i, f"{i}σ") for i in range(int(SIGMA_CEILING) + 1)],
    )
```

`MitigationEpisodeLike` in the annotation above is a placeholder the
implementer must not copy: type the dict as `dict[int, list]`. Add
`import math` at the top of `charts.py` if it is not already there, and note
that `HourlyPoint` must accept the thirteen bin attributes for
`getattr(point, "n300")` to find anything - see Step 4.

- [ ] **Step 4: Let the hourly point carry its own bins**

`HourlyPoint` in `services/backend/schemas/history.py` declares
`hour_start`, `requests` and `batches` only, so the bins written by
`record_traffic` are dropped by `list_series` exactly as `last_features` was
dropped by `list_history`. Add to the model:

```python
    # The thirteen near-threshold bins, written by record_traffic onto the
    # same hourly row. Declared individually rather than as a dict because
    # this model is also the JSON API's response shape, and a free-form dict
    # there would be an unversioned contract.
    model_config = ConfigDict(extra="allow")
```

and have `list_series` pass every `n###` attribute through. Verify with a
test in the same file that a bin written by `record_traffic` survives
`list_series`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_history_grid.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/backend/ui/charts.py services/backend/schemas/history.py \
        services/backend/api/routes/dashboard.py \
        services/backend/tests/test_history_grid.py
git commit -m "feat(charts): the sigma axis rotated, one row per hour"
```

---

## Task 2: The grid on the page, and the deviation chart gone

**Files:**
- Create: `services/backend/ui/templates/_grid.html`
- Modify: `services/backend/ui/templates/dashboard_history.html`
- Modify: `services/backend/ui/dashboard.py` (`history_page`)
- Modify: `services/backend/ui/static/app.css`
- Test: `services/backend/tests/test_history_screen.py` (create)

**Interfaces:**
- Consumes: `Grid` from Task 1, `axis_state(health, throttle, model_ready)`
  from `presenters.py`.
- Produces: nothing new; `deviation_chart` keeps its one remaining caller in
  `public.py` until Phase 3 retires the landing page's copy.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_history_screen.py` with a client fixture
copied from `test_historic_decomposition.py` (same tenant, same agent, same
`ModelManager._cache.clear()`), and these assertions:

```python
def test_the_history_screen_draws_the_grid(client, dynamo_resource):
    _hours_of_traffic(dynamo_resource)

    assert "c-grid" in client.get("/dashboard/ui/history").text


def test_the_deviation_chart_is_gone_from_the_console(client, dynamo_resource):
    """Spec 3.4: the grid REPLACES it. Two charts of the same window in two
    shapes is the dashboard failure this rebuild exists to undo."""
    _hours_of_traffic(dynamo_resource)

    assert "Worst deviation per hour" not in client.get("/dashboard/ui/history").text


def test_the_gates_cut_through_the_whole_grid(client, dynamo_resource):
    _hours_of_traffic(dynamo_resource)
    page = client.get("/dashboard/ui/history").text

    assert page.count("c-grid-gate") == 2


def test_an_hour_with_no_telemetry_is_drawn_as_a_hole(client, dynamo_resource):
    _hours_of_traffic(dynamo_resource)

    assert "c-grid-row--dead" in client.get("/dashboard/ui/history").text


def test_the_grid_carries_a_table_for_a_screen_reader(client, dynamo_resource):
    """Position in a picture reads as nothing. A summary, not a matrix: 312
    cell counts read aloud answer no question anyone has."""
    _hours_of_traffic(dynamo_resource)
    page = client.get("/dashboard/ui/history").text

    assert "<caption>" in page[page.index("c-grid"):]


def test_the_grid_offers_no_link_to_the_sub_threshold_sources(client, dynamo_resource):
    """Spec 5.3. No address below the gate is stored, only the shape."""
    _hours_of_traffic(dynamo_resource)

    assert "show me" not in client.get("/dashboard/ui/history").text.lower()


def test_a_window_with_no_telemetry_at_all_says_so_rather_than_drawing_bands(client):
    """Principle 1.2. An instrument with no feed does not render a reading."""
    page = client.get("/dashboard/ui/history").text

    assert "c-grid-cell" not in page
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_history_screen.py -q`

- [ ] **Step 3: Write the macro**

Create `services/backend/ui/templates/_grid.html`:

```jinja
{% macro render(g, label, caption="") %}
{# The sigma axis, rotated and stacked.

   Two layers, exactly as the horizontal axis: SVG for everything at an
   arbitrary position and nothing with text, HTML for all the type at integer
   sigma. The gates are two lines through the full height, which is the whole
   point of the shape - an operator sees the gate literally cutting across
   their own history.

   Only cells with a count are emitted. A dense 168-row grid emitting every
   empty bin is about 22KB of markup describing absence. #}
<div class="c-grid" role="img" aria-label="{{ label }}">
  <svg class="c-grid-plot" viewBox="0 0 {{ g.width }} {{ g.height }}"
       preserveAspectRatio="none" focusable="false" aria-hidden="true">
    {% for b in g.bands %}
    <rect class="{{ b.css }}" x="{{ b.x }}" y="0"
          width="{{ b.width }}" height="{{ g.height }}"/>
    {% endfor %}

    {% for r in g.rows %}
    {% if not r.live %}
    <rect class="c-grid-row--dead" x="0" y="{{ r.y }}"
          width="{{ g.width }}" height="{{ g.row_height }}"/>
    {% endif %}
    {% for c in r.cells %}
    <rect class="c-grid-cell{% if c.identified %} c-grid-cell--id{% endif %}"
          data-level="{{ c.level }}" x="{{ c.x }}" y="{{ r.y }}"
          width="{{ c.width }}" height="{{ g.row_height }}"/>
    {% endfor %}
    {% endfor %}

    <line class="c-grid-gate c-grid-gate--1" x1="{{ g.gate1_x }}" y1="0"
          x2="{{ g.gate1_x }}" y2="{{ g.height }}"/>
    <line class="c-grid-gate c-grid-gate--2" x1="{{ g.gate2_x }}" y1="0"
          x2="{{ g.gate2_x }}" y2="{{ g.height }}"/>
  </svg>

  <div class="c-grid-ticks">
    {% for value, text in g.ticks %}<span class="c-axis-tick">{{ text }}</span>{% endfor %}
  </div>
</div>

{% if caption %}<p class="c-axis-caption">{{ caption }}</p>{% endif %}

{# A SUMMARY, not a matrix. Three hundred and twelve cell counts read aloud
   answer no question anyone has. #}
<div class="sr-only">
  <table>
    <caption>{{ label }}</caption>
    <thead>
      <tr><th scope="col">Hours</th><th scope="col">Count</th></tr>
    </thead>
    <tbody>
      <tr><th scope="row">With telemetry</th>
          <td>{{ g.rows|selectattr("live")|list|length }}</td></tr>
      <tr><th scope="row">With a source past {{ "%.2f"|format(g.gate1_sigma) }} sigma</th>
          <td>{{ g.rows|map(attribute="cells")|map("selectattr", "identified")|map("list")|select|list|length }}</td></tr>
    </tbody>
  </table>
</div>
{% endmacro %}
```

If that last Jinja expression will not evaluate cleanly, compute the two
figures in `history_page` and pass them in. A template expression nobody can
read is worse than two integers in the context.

- [ ] **Step 4: Write the CSS**

Append to `app.css`. Five bucketed levels, four rules:

```css
/* --- history: the axis rotated ----------------------------------------- */

.c-grid { margin-top: var(--space-3); }
.c-grid-plot { width: 100%; height: 260px; display: block; }
.c-grid-ticks {
  display: grid; grid-template-columns: repeat(7, 1fr);
  font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--text-muted);
}
.c-grid-row--dead { fill: var(--surface-sunken); opacity: .8; }
/* Bucketed, not measured: four steps. Opacity rather than four colours,
 * because the bands underneath already carry meaning in colour and a second
 * meaning on the same channel is unreadable. */
.c-grid-cell { fill: var(--text); }
[data-level="1"] { fill-opacity: .25; }
[data-level="2"] { fill-opacity: .5; }
[data-level="3"] { fill-opacity: .75; }
[data-level="4"] { fill-opacity: 1; }
.c-grid-cell--id { fill: var(--tier-blocked); }
.c-grid-gate { stroke: var(--accent); stroke-width: 2; }
.c-grid-gate--2 { stroke-dasharray: 4 3; }
```

- [ ] **Step 5: Swap it in**

In `history_page`, build the grid from the `series` and `episodes` already
queried, using the tenant's own gates read the same way `protection_status`
reads them (from the Tenants item, not the model's copy). Delete the
`deviation_chart` and `downsample` call and the `worst`/`points` loop that
feeds it, and drop both from the import if `public.py` is their only other
caller.

Show the grid only when there is telemetry in the window: `axis_state` already
decides this for the status page, and an instrument with no feed does not
render a reading.

- [ ] **Step 6: Run everything, lint, check the budget, commit**

```bash
PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q \
  --cov=services/backend --cov=services/agent --cov-fail-under=80
ruff check services/backend services/agent
wc -c services/backend/ui/static/*.css
git add services/backend/ui/ services/backend/tests/test_history_screen.py
git commit -m "feat(console): history is the same ruler, rotated"
```

---

## Task 3: Decided and in effect are two states, permanently

Principle 1.5, and the one thing the spec says must be *built in rather than
patched on*. The agent is asynchronous by architecture: the backend decides,
the agent collects on its next poll, and until it does the customer's nginx
has not changed. A screen that shows one state is wrong about the other half
of the time it matters.

There is no acknowledgement channel and this task must not invent one. What
exists is `AgentsTable.last_seen_at`, touched by `agent_auth` on **every**
authenticated agent request including `/agent/v1/decisions`. So "the agent has
collected decisions since this one was made" is derivable, honestly, from data
the status page already reads.

**Files:**
- Modify: `services/backend/ui/presenters.py` (`enforcement_state`)
- Modify: `services/backend/ui/dashboard.py`
- Modify: `services/backend/ui/templates/dashboard_status.html`
- Modify: `services/backend/ui/static/app.css`
- Test: `services/backend/tests/test_decided_and_in_effect.py` (create)

**Interfaces:**
- Consumes: `agent_health(agents, now)` and the agent rows it reads.
- Produces: `presenters.reach(decided_at: int, agents: list[dict], now) ->
  dict` with keys `in_effect: bool`, `label: str`, `detail: str`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_decided_and_in_effect.py`:

```python
"""Two states, permanently.

The agent is asynchronous by architecture. The backend writes a decision and
the customer's nginx does not change until the agent next collects, so
"blocked" is two facts and a screen showing one of them is wrong about the
other half of the time it matters.

There is no acknowledgement channel, and this must not invent one. What there
is: `agent_auth` touches `last_seen_at` on every authenticated agent request,
including the decisions poll. "Has this agent collected anything since the
decision was made" is therefore a real measurement, and it is the strongest
claim the stored data supports.
"""
from datetime import datetime, timedelta, timezone

from services.backend.ui.presenters import reach


def _agent(seen_ago_seconds, now):
    return {"agent_id": "a-1", "agent_label": "web-01",
            "last_seen_at": (now - timedelta(seconds=seen_ago_seconds)).isoformat()}


def test_an_agent_that_has_polled_since_the_decision_has_it():
    now = datetime.now(timezone.utc)
    decided = int((now - timedelta(minutes=5)).timestamp())

    assert reach(decided, [_agent(30, now)], now)["in_effect"] is True


def test_an_agent_that_has_not_polled_since_the_decision_does_not_have_it_yet():
    now = datetime.now(timezone.utc)
    decided = int((now - timedelta(seconds=10)).timestamp())

    assert reach(decided, [_agent(600, now)], now)["in_effect"] is False


def test_one_stale_agent_out_of_three_means_not_everywhere():
    """A fleet is protected at the pace of its slowest member. Reporting
    "in effect" because two of three have it would be the more comfortable
    claim and the false one."""
    now = datetime.now(timezone.utc)
    decided = int((now - timedelta(minutes=5)).timestamp())
    agents = [_agent(30, now), _agent(30, now), _agent(3600, now)]

    state = reach(decided, agents, now)

    assert state["in_effect"] is False
    assert "1 of 3" in state["detail"]


def test_no_agents_at_all_is_not_in_effect_and_says_why():
    now = datetime.now(timezone.utc)

    state = reach(int(now.timestamp()), [], now)

    assert state["in_effect"] is False
    assert "no agent" in state["detail"].lower()


def test_an_agent_with_an_unreadable_timestamp_counts_as_not_reached():
    """A parse failure must fail closed. Counting it as reached would report
    a customer protected on the strength of a field nobody could read."""
    now = datetime.now(timezone.utc)

    state = reach(int(now.timestamp()) - 60,
                  [{"agent_id": "a-1", "last_seen_at": "not a date"}], now)

    assert state["in_effect"] is False


def test_the_label_never_claims_more_than_the_data_supports():
    """It says the agent collected, not that nginx applied it. There is no
    acknowledgement channel and a label implying one would be a promise the
    product cannot keep."""
    now = datetime.now(timezone.utc)

    state = reach(int((now - timedelta(minutes=5)).timestamp()),
                  [_agent(30, now)], now)

    assert "confirmed" not in state["label"].lower()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_decided_and_in_effect.py -q`
Expected: `ImportError: cannot import name 'reach'`

- [ ] **Step 3: Write it**

Add to `services/backend/ui/presenters.py`:

```python
def reach(decided_at: int, agents: list[dict], now: datetime) -> dict:
    """Has every agent collected decisions since this one was made.

    Principle 1.5: decided and in effect are two states, permanently. The
    agent is asynchronous by architecture, so a decision written here has not
    changed the customer's nginx until the agent next polls - and there is no
    acknowledgement channel, so this must not claim one.

    `last_seen_at` is touched by agent_auth on every authenticated agent
    request, including the decisions poll, so "has collected since" is a real
    measurement of the strongest claim the data supports. It says the agent
    collected; it does not say nginx applied it, and the wording has to keep
    that distinction because the product cannot see past the collection.

    A fleet is protected at the pace of its slowest member, so this is an
    ALL, not a majority. An unreadable timestamp counts as not reached: a
    parse failure reporting a customer protected is the worst way to be
    wrong.
    """
    if not agents:
        return {"in_effect": False, "label": "Not in effect",
                "detail": "No agent has collected this yet."}

    reached = 0
    for agent in agents:
        try:
            seen = datetime.fromisoformat(str(agent.get("last_seen_at", "")))
        except ValueError:
            continue
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        if int(seen.timestamp()) >= int(decided_at):
            reached += 1

    total = len(agents)
    if reached == total:
        return {"in_effect": True, "label": "In effect",
                "detail": f"Collected by {total} agent"
                          f"{'' if total == 1 else 's'}."}
    return {"in_effect": False, "label": "Not everywhere yet",
            "detail": f"{total - reached} of {total} agent"
                      f"{'' if total == 1 else 's'} has not collected this yet."}
```

Note the phrasing the tests pin: the stale-agent case reports how many have
**not** collected, so `"1 of 3"` appears for one stale agent out of three.

- [ ] **Step 4: Put it on the row and in the pane**

`_rows` in `dashboard.py` gains a `reach` key per mitigation, built from the
agent rows `agent_health` already read - no extra query. The mitigation table
gains a state chip beside the tier chip, and the detail pane states both:

```jinja
        <span class="c-state c-state--{{ 'blocked' if selected.tier >= 2 else 'slowed' }}">{{ selected.tier_label }}</span>
        <span class="c-reach{{ ' c-reach--on' if selected.reach.in_effect }}">{{ selected.reach.label }}</span>
```

with `selected.reach.detail` in the Context list beside the other facts.

- [ ] **Step 5: Style it**

```css
/* Decided and in effect are two states, permanently: the agent is
 * asynchronous by architecture. Never colour alone - this one has to survive
 * a screenshot pasted into a ticket more than most. */
.c-reach {
  font-size: var(--text-xs); padding: 1px var(--space-2);
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text-muted);
}
.c-reach--on { border-color: var(--tier-normal); color: var(--text); }
```

- [ ] **Step 6: Run everything, lint, commit**

```bash
PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q
ruff check services/backend services/agent
git add services/backend/ui/ services/backend/tests/test_decided_and_in_effect.py
git commit -m "feat(console): decided and in effect are two states on every row"
```

---

## Task 4: The block gate, where it has evidence

Spec §5.2. The tier-1 gate is tuned on 24 hours; the tier-2 gate cannot be,
because ADR-006 measured 0.00% false positives below z = -5.0, so the 5.0 to
6.0 bins are empty on almost every day and the curve has no evidence at the
place the operator is being asked to put the line.

Seven days of bins is roughly 20 RCU per view, which is why it goes behind the
`?days=7` tab that already exists and is not switched on everywhere.

**Files:**
- Modify: `services/backend/ui/dashboard.py` (`history_page`)
- Modify: `services/backend/ui/templates/dashboard_history.html`
- Test: `services/backend/tests/test_block_gate.py` (create)

**Interfaces:**
- Consumes: `gate_curve(bins, current_sigma, blocked_total)` and the
  `POST /dashboard/ui/gate?tier=2&sigma=` route, both from Phase 1b.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_block_gate.py`, client fixture as before:

```python
def test_the_block_gate_is_not_offered_on_the_24_hour_view(client, dynamo_resource):
    """ADR-006 measured 0.00% false positives below 5 sigma, so those bins
    are empty on almost every day and the curve would have no evidence at
    the very place the operator is being asked to put the line."""
    _trained(dynamo_resource)

    assert "tier=2" not in client.get("/dashboard/ui/history?days=1").text


def test_the_block_gate_is_offered_on_the_7_day_view(client, dynamo_resource):
    _trained(dynamo_resource)

    assert "tier=2" in client.get("/dashboard/ui/history?days=7").text


def test_it_says_which_window_the_counts_came_from(client, dynamo_resource):
    """The same control on the status page counts 24 hours. Two identical
    curves reporting different numbers with nothing saying why is how an
    operator concludes the product is broken."""
    _trained(dynamo_resource)

    assert "7 days" in client.get("/dashboard/ui/history?days=7").text


def test_a_tenant_with_no_model_is_not_offered_the_block_gate(client):
    assert "tier=2" not in client.get("/dashboard/ui/history?days=7").text


def test_the_block_gate_curve_counts_seven_days_of_bins(client, dynamo_resource):
    _trained(dynamo_resource)
    _bins_across_days(dynamo_resource)      # 3 in one day, 4 six days back

    page = client.get("/dashboard/ui/history?days=7").text
    control = page[page.index("c-gate-curve"):]

    assert "7" in control
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_block_gate.py -q`

- [ ] **Step 3: Build the curve on the 7-day view only**

In `history_page`, when `days == 7` and the gates are armed, fold the bins out
of the `series` rows already queried exactly as `protection_status` does, and
build `gate_curve(bins, tier2_sigma, blocked_total)` where `blocked_total` is
the count of episodes in the window with `tier2_count`. Render the existing
`_gate.html` macro with `tier=2`.

- [ ] **Step 4: Say where the numbers came from**

The 24-hour control on the status page and this one are the same macro
reporting different windows. The panel label states the window, and the
status-page panel gains the matching words, so the two can never be read as
disagreeing.

- [ ] **Step 5: Run everything, lint, commit**

```bash
PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q \
  --cov=services/backend --cov=services/agent --cov-fail-under=80
ruff check services/backend services/agent
git add services/backend/ui/ services/backend/tests/test_block_gate.py
git commit -m "feat(console): the block gate, on the window that has evidence for it"
```

---

## Self-review

**Spec coverage.** §3.4 is Tasks 1 and 2, including its hard requirement that
only populated cells are emitted and its instruction that the grid replaces
the deviation chart. §4.4's filtering defect was fixed in Phase 1c; its
remaining content, episodes living on the history screen, already ships. §5.2
is Task 4. §5.3's first half (a count and a ratio, never a "show me them"
link) is asserted in Task 2; its second half, the gate showing both issued and
in-effect states, is Task 3. §1.5 is Task 3.

**Type consistency.** `Grid`/`GridRow`/`GridCell` (Task 1) are produced only
by `history_grid` and consumed only by `_grid.html`. `reach` (Task 3) returns
a dict with `in_effect`, `label`, `detail`, read in both the row and the pane.
Task 4 reuses `gate_curve` and `_gate.html` unchanged, which is the point: a
second control shaped differently from the first would be a second thing to
learn.

**Known risks, stated rather than designed around.** Task 1 Step 4 changes
`HourlyPoint`, which is also a JSON API response model, so the bin attributes
become part of that contract. Task 4 adds no query but reads 7 days of series
rows that the `?days=7` tab already fetches. Task 3 depends on `last_seen_at`
being touched by the decisions poll; if that ever stops being true, the
"in effect" claim becomes false silently, so the test suite must pin the touch
itself and not only the presenter.


---

## What the plan got wrong

Written after execution, against the shipped code.

**1. `list_series` dropped the bins, exactly as `list_history` dropped the
feature vectors.**

The same defect twice, one phase apart. `record_traffic` has written the
thirteen near-threshold bins onto the hourly row since Phase 0, and
`HourlyPoint` declared `hour_start`, `requests` and `batches`, so every bin
was read out of DynamoDB and thrown away in the same function. A geometry test
passes either way, because it is handed a model somebody constructed by hand -
which is why the shipped test suite now pins the read path itself.

The plan proposed `model_config = ConfigDict(extra="allow")`. Shipped instead
as a declared `near: dict[str, int]` field, because `HourlyPoint` is also a
JSON API response shape and an undeclared attribute there is an unversioned
contract. One field rather than thirteen, because the bin edges are defined
once on `TenantHistoryTable.NEAR_BINS` and thirteen names in the schema would
be a second copy of that list to keep in step.

**2. Principle 1.5 was not merely unimplemented. It was unimplementable.**

`MitigationState` carried `expires_at` and no record of when the decision was
taken, so "has the agent collected this yet" had no left-hand side. Task 3
adds `decided_at`, one integer on a write that already happens.

**3. Two states were not enough.**

The plan's `reach` returned in-effect or not. A row written before `decided_at`
existed supports neither answer, and reporting "not in effect" would have put
a warning on every decision this product has ever taken. The shipped version
has a third: "we cannot tell", said plainly.

**4. The empty-window test predated principle 1.2 and had to be split.**

`test_an_empty_window_still_renders_the_chart_chrome` asserted that a window
with nothing in it still draws bands, on the argument that the healthy state
of this product is empty and a blank panel is not evidence. That argument is
right for a QUIET window - telemetry arriving, nothing crossing a gate - and
wrong for a window with no telemetry at all, which is an instrument with no
feed rendering a reading. The test seeded nothing and demanded the chrome, so
it was testing the second case while arguing the first. Split in two.

**5. The Protection panel was still called "Where you act" while a second
control of exactly the same shape was added to History.**

Two panels labelled for the screen they sit on rather than the gate they move
is how an operator blocks at the line they meant to slow at. Renamed to "Where
you slow" and "Where you block", and each now names the window its counts come
from, because the two curves genuinely report different numbers.

**6. Minor.** The plan's macro emitted `c-grid-row--dead` and the shipped one
emits `c-grid-dead`. The plan's screen-reader summary used a Jinja expression
nobody could read; it is a `hours_with_sources` property on `Grid` instead. And
a test counting `c-grid-cell` as a bare substring double-counts every
identified cell, because `c-grid-cell--id` contains it.

## Still outstanding after this phase

- `deviation_chart` and `downsample` keep one caller each, on the landing page.
  Phase 3 decides whether the landing page gets the grid or keeps its own
  shape; until then `charts.py` carries both.
- Two of spec 2.5's four audited actions are still unwired: tenant
  suspend/reactivate and agent key mint/revoke, both in the operations console.
- ADR-007's htmx configuration still needs confirming in a real browser.
- Nothing in Phases 1b, 1c or 1d has been deployed. Production is also still
  missing `bulk-select.js` and `keys.js`.
- A gate move reaches enforcement at the next nightly retrain. The zero-RCU
  path to making it immediate is written up at the end of the Phase 1b plan.
