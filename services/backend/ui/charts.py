"""Charts, as geometry. No dependency, no build step, no canvas.

Every chart in this product is server-generated inline SVG. That is not a
compromise — it is the only option that fits, and it happens to be the right
one:

  * A charting library would be a second dependency tree `pip-audit` cannot
    audit, in a package already at 200MB of a 250MB limit.
  * The CSP is `script-src 'self'` with no external origins, so a CDN is not
    reachable, and `style-src 'self'` means a library that injects inline
    styles would force the policy back open.
  * Inline SVG keeps the numbers in the HTML, which is what lets the
    tenant-isolation assertions (`assert "9.9.9.9" not in resp.text`) keep
    meaning something on charted data. A pure-geometry canvas would defeat
    them silently.

The constraint that shaped this module: **`style=` attributes are blocked by
CSP, but SVG presentation attributes are not.** `x`, `y`, `width`, `points`,
`d`, `fill` are not CSS and the policy does not apply to them. That is the
fact that makes arbitrary computed geometry possible here at all — the
portal previously worked around the block with 21 hardcoded
`[data-width="N"]` rules and rounded real percentages to the nearest 5.

So: geometry goes in attributes, colour goes in `class`. Hardcoding a hex
would break theming; a class picks up a token and themes for free, prints
correctly, and follows a future high-contrast mode.

This module returns plain data. The markup lives in `_charts.html`, which
keeps the templates readable and lets these functions be tested on numbers
rather than on strings of angle brackets.
"""
import math
from dataclasses import dataclass, field

# Thresholds, in standard deviations below the tenant's own mean. Mirrors
# ml.model.TIER1_Z / TIER2_Z, imported rather than duplicated so a
# recalibration cannot leave the chart drawing last quarter's lines.
from services.backend.ml.model import TIER1_Z, TIER2_Z

# The deviation chart plots |z|, so taller is worse. Asking a reader to
# interpret a downward axis where "more negative is more dangerous" is a
# needless inversion.
TIER1_SIGMA = abs(TIER1_Z)
TIER2_SIGMA = abs(TIER2_Z)

# Anything past this is drawn at the ceiling with an overflow cap, rather
# than rescaling the whole chart around one outlier and flattening the rest.
SIGMA_CEILING = 6.0


@dataclass
class Bar:
    x: float
    y: float
    width: float
    height: float
    css: str
    label: str
    value: float
    clipped: bool = False


@dataclass
class Rule:
    y: float
    label: str
    css: str = "c-rule"


@dataclass
class DeviationChart:
    """Columns of |z| over time. One bar per bucket, tallest = most unusual."""

    width: float = 640
    height: float = 200
    pad_left: float = 34
    pad_right: float = 8
    pad_top: float = 16
    pad_bottom: float = 22

    bars: list[Bar] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    ticks: list[tuple[float, str]] = field(default_factory=list)
    anchors: list[tuple[float, str]] = field(default_factory=list)
    band: tuple[float, float] | None = None
    empty_message: str = ""
    caption: str = ""
    rows: list[tuple[str, str]] = field(default_factory=list)

    @property
    def plot_width(self) -> float:
        return self.width - self.pad_left - self.pad_right

    @property
    def plot_height(self) -> float:
        return self.height - self.pad_top - self.pad_bottom


def _tier_css(sigma: float) -> str:
    if sigma >= TIER2_SIGMA:
        return "c-bar c-bar--blocked"
    if sigma >= TIER1_SIGMA:
        return "c-bar c-bar--limited"
    return "c-bar c-bar--normal"


def deviation_chart(points: list[tuple[int, float | None]], *,
                    empty_message: str = "", caption: str = "",
                    label_for=None) -> DeviationChart:
    """`points` is [(epoch_seconds, sigma_or_None)] oldest first.

    `None` means "we had no data for this bucket", which is different from
    zero and must not be drawn as a floor-height bar: a dead agent would
    otherwise render as a calm, healthy baseline.
    """
    chart = DeviationChart(empty_message=empty_message, caption=caption)
    chart.band = (_y(TIER1_SIGMA, chart), _y(0.0, chart))
    chart.rules = [
        Rule(_y(TIER1_SIGMA, chart), f"{TIER1_SIGMA:.0f}σ"),
        Rule(_y(TIER2_SIGMA, chart), f"{TIER2_SIGMA:.0f}σ"),
    ]
    chart.ticks = [(_y(v, chart), "0" if v == 0 else f"{v:.0f}σ")
                   for v in (0.0, 3.0, TIER1_SIGMA, TIER2_SIGMA)]

    if not points:
        return chart

    slot = chart.plot_width / len(points)
    gap = min(2.0, slot * 0.25)
    bar_w = max(1.0, slot - gap)

    for i, (ts, sigma) in enumerate(points):
        if sigma is None:
            continue
        magnitude = abs(sigma)
        clipped = magnitude > SIGMA_CEILING
        drawn = min(magnitude, SIGMA_CEILING)
        y = _y(drawn, chart)
        text = label_for(ts) if label_for else str(ts)
        chart.bars.append(Bar(
            x=chart.pad_left + i * slot + gap / 2,
            y=y,
            width=bar_w,
            height=(chart.pad_top + chart.plot_height) - y,
            css=_tier_css(magnitude),
            label=text,
            value=round(magnitude, 2),
            clipped=clipped,
        ))
        chart.rows.append((text, f"{magnitude:.1f}σ"))

    if points:
        first, last = points[0][0], points[-1][0]
        mid = points[len(points) // 2][0]
        for pos, ts in ((chart.pad_left, first),
                        (chart.pad_left + chart.plot_width / 2, mid),
                        (chart.pad_left + chart.plot_width, last)):
            chart.anchors.append((pos, label_for(ts) if label_for else str(ts)))

    return chart


def _y(sigma: float, chart: DeviationChart) -> float:
    """viewBox y for a sigma magnitude. y grows downward, sigma grows up."""
    fraction = min(sigma, SIGMA_CEILING) / SIGMA_CEILING
    return chart.pad_top + chart.plot_height * (1 - fraction)


# --- the enforcement stack ------------------------------------------------

@dataclass
class StackBar:
    x: float
    width: float
    css: str
    label: str
    count: int


def enforcement_stack(normal: int, slowed: int, blocked: int,
                      width: float = 320) -> list[StackBar]:
    """A single horizontal bar split three ways. Zero-width segments are
    dropped rather than rendered as slivers, which read as noise and can
    still catch a tooltip."""
    total = normal + slowed + blocked
    if total <= 0:
        return []
    out: list[StackBar] = []
    x = 0.0
    for count, css, label in ((normal, "c-stack c-stack--normal", "normal"),
                              (slowed, "c-stack c-stack--limited", "slowed"),
                              (blocked, "c-stack c-stack--blocked", "blocked")):
        if count <= 0:
            continue
        w = width * count / total
        out.append(StackBar(x=x, width=w, css=css, label=label, count=count))
        x += w
    return out


# --- sparkline ------------------------------------------------------------

def sparkline(values: list[float], width: float = 80, height: float = 20) -> str:
    """A `points` string for a <polyline>. Empty for fewer than two points:
    a one-point line is a dot pretending to be a trend."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = width / (len(values) - 1)
    return " ".join(
        f"{i * step:.1f},{height - (v - lo) / span * height:.1f}"
        for i, v in enumerate(values)
    )


def downsample(points: list, limit: int = 240) -> list:
    """Never emit one SVG element per data row.

    24 hours of 5-second buckets is 17,280 points: ~207KB of `points=`
    markup for a chart at most 1,200px wide, i.e. fourteen points per pixel,
    thirteen of them invisible. Keeping the extremes rather than averaging
    matters here — an attack is a spike, and a mean would erase exactly the
    thing the chart exists to show.
    """
    if len(points) <= limit:
        return points
    stride = len(points) / limit
    out = []
    for i in range(limit):
        chunk = points[int(i * stride):int((i + 1) * stride)] or None
        if not chunk:
            continue
        worst = max(chunk, key=lambda p: abs(p[1]) if p[1] is not None else -1)
        out.append(worst)
    return out


# --- the axis -------------------------------------------------------------

# 100 units per sigma, so the viewBox is 0..600 for 0..6 sigma. A round
# number because a person checking the markup by eye should be able to do the
# division.
AXIS_UNITS_PER_SIGMA = 100


def axis_x(sigma: float) -> float:
    """Where a magnitude sits, clamped at the ceiling.

    Takes |sigma|: z is negative by convention and the axis runs 0 to 6, so a
    raw -6.4 would land off the left edge without complaining.

    Clamped rather than rescaled. One source at 40 sigma would otherwise
    flatten every other source into the first two pixels, which is how a
    chart hides the thing it was drawn to show.
    """
    return min(abs(sigma), SIGMA_CEILING) * AXIS_UNITS_PER_SIGMA


@dataclass
class Band:
    x: float
    width: float
    css: str
    label: str
    short: str


def axis_bands(tier1_sigma: float, tier2_sigma: float) -> list[Band]:
    """The three regions, drawn from THIS tenant's gates.

    Not from the module constants. Bands drawn from those would show every
    tenant somebody else's gate, which is the exact thing the per-tenant
    threshold exists to stop.

    Labels are written in the case they are read in. A stylesheet that
    uppercases them turns sigma into Sigma, which is summation, and that
    reached an incident screen once already.
    """
    edges = [(0.0, tier1_sigma, "c-seg c-seg--normal",
              "YOUR NORMAL TRAFFIC", "NORMAL"),
             (tier1_sigma, tier2_sigma, "c-seg c-seg--limited",
              f"{tier1_sigma:.0f}σ SLOWED", f"{tier1_sigma:.0f}σ"),
             (tier2_sigma, SIGMA_CEILING, "c-seg c-seg--blocked",
              f"{tier2_sigma:.0f}σ BLOCKED", f"{tier2_sigma:.0f}σ")]
    return [Band(x=axis_x(lo), width=axis_x(hi) - axis_x(lo), css=css,
                 label=label, short=short)
            for lo, hi, css, label, short in edges]


@dataclass
class DensityBar:
    x: float
    width: float
    height: float     # 0..1, relative to the tallest bar
    css: str
    sigma: float
    count: int


def density_bars(bins: dict[str, int]) -> list[DensityBar]:
    """The shape below the gate, from the thirteen recorded bins.

    Every bin is emitted, including the ones with no count. ADD only writes
    the bins a batch touched, so an untouched bin comes back ABSENT rather
    than zero, and a chart that skips it redraws the axis with the wrong
    shape - the same zero-fill query_series(fill=True) already does.

    Heights are relative, because absolute counts cannot be drawn on one
    scale: one tenant sees four near-misses an hour and another sees four
    thousand. The shape is the information, not the magnitude.
    """
    from services.backend.core.tables import TenantHistoryTable

    edges = TenantHistoryTable.NEAR_BINS
    counts = [int(bins.get("n%d" % round(s * 100), 0)) for s in edges]
    tallest = max(counts) if counts else 0
    step = (edges[1] - edges[0]) if len(edges) > 1 else 0.25

    return [DensityBar(x=axis_x(s), width=axis_x(s + step) - axis_x(s),
                       height=(c / tallest) if tallest else 0.0,
                       css="c-density", sigma=s, count=c)
            for s, c in zip(edges, counts)]


@dataclass
class Mark:
    x: float
    sigma: float
    css: str
    ip: str
    # Selection is a second state ON TOP of severity, never instead of it. A
    # selected blocked source that stops rendering as blocked would be a lie
    # about what was done to it.
    selected: bool = False


def source_marks(rows: list[dict],
                 selected_ip: str | None = None) -> list[Mark]:
    """One tick per identified source, above the gate.

    A row whose z is None is dropped rather than placed. z is None when the
    model had no usable spread, and drawing it at zero would put a blocked
    source inside the band labelled "your normal traffic".

    `selected_ip` lights the source the operator opened. Without it the one
    picture on the screen does not react to the selection at all, and the
    detail pane reads as a separate page that happens to sit beside it.
    """
    marks = []
    for row in rows:
        z = row.get("z")
        if z is None:
            continue
        ip = row.get("ip", "")
        marks.append(Mark(x=axis_x(z), sigma=abs(z),
                          css=_tier_css(abs(z)).replace("c-bar", "c-mark"),
                          ip=ip,
                          # `?ip=` with nothing after it arrives as an empty
                          # string, which would otherwise match every row
                          # whose ip is also missing.
                          selected=bool(selected_ip) and ip == selected_ip))
    return marks


@dataclass
class Axis:
    width: float
    height: float
    bands: list[Band] = field(default_factory=list)
    density: list[DensityBar] = field(default_factory=list)
    marks: list[Mark] = field(default_factory=list)
    ticks: list[tuple[int, str]] = field(default_factory=list)
    gate1_x: float = 0.0
    gate2_x: float = 0.0
    gate1_sigma: float = 0.0
    gate2_sigma: float = 0.0


def build_axis(tier1_sigma: float, tier2_sigma: float,
               bins: dict[str, int], rows: list[dict],
               selected_ip: str | None = None) -> Axis:
    """Everything the macro needs, in one object.

    Its size is constant in the number of sources below the gate: those
    become thirteen density bars whatever their count, and only the sources
    above the gate - the mitigation list, already bounded - become individual
    marks.
    """
    return Axis(
        width=SIGMA_CEILING * AXIS_UNITS_PER_SIGMA,
        height=48,
        bands=axis_bands(tier1_sigma, tier2_sigma),
        density=density_bars(bins),
        marks=source_marks(rows, selected_ip),
        # Integer sigma only. Sitting at integers is precisely why these need
        # no computed position, and therefore no enumerated CSS rule: in the
        # HTML layer they are grid columns.
        ticks=[(i, f"{i}σ") for i in range(int(SIGMA_CEILING) + 1)],
        gate1_x=axis_x(tier1_sigma),
        gate2_x=axis_x(tier2_sigma),
        gate1_sigma=tier1_sigma,
        gate2_sigma=tier2_sigma,
    )


@dataclass
class GateOption:
    sigma: float
    count: int
    width: float        # 0..1, relative to the widest row
    current: bool
    recommended: bool


# Below this the measured false-positive rate climbs steeply: ADR-006 found
# 0.82% at 3.5 sigma against 0.27% at 4.0. The control may offer these
# positions - a tenant with a genuinely narrow baseline may want one - and
# must not present them as ordinary.
GATE_RECOMMENDED_FLOOR = 4.0


def gate_curve(bins: dict[str, int], current_sigma: float,
               blocked_total: int = 0) -> list[GateOption]:
    """What each of the thirteen legal gates would have caught.

    Cumulative on purpose. "How many sources sit in the 3.5 bin" is trivia;
    "how many would a gate here catch" is the decision, and that is every
    source at or past the line - including the ones already past the current
    gate, which is what `blocked_total` adds. Leaving those out would tell an
    operator that lowering the gate catches FEWER sources than raising it,
    which is backwards.

    A rendered curve rather than a slider, because there are thirteen
    answers and a slider shows one of them at a time.
    """
    from services.backend.core.tables import TenantHistoryTable

    edges = TenantHistoryTable.NEAR_BINS
    counts = [int(bins.get("n%d" % round(s * 100), 0)) for s in edges]

    # Walk down from the ceiling so each edge learns what is strictly above
    # it, then fold in the bin sitting on the line itself.
    above: list[int] = []
    running = int(blocked_total)
    for count in reversed(counts):
        above.append(running)
        running += count
    above.reverse()
    totals = [a + c for a, c in zip(above, counts)]

    widest = max(totals) if totals else 0
    return [GateOption(sigma=s, count=t,
                       width=(t / widest) if widest else 0.0,
                       current=(abs(s - current_sigma) < 1e-9),
                       recommended=(s >= GATE_RECOMMENDED_FLOOR))
            for s, t in zip(edges, totals)]


# --- zoom 2: one feature of one source, hour by hour -----------------------

FEATURE_TRACK_WIDTH = 600.0
FEATURE_TRACK_HEIGHT = 120.0
# The same ceiling the sigma axis uses, so a point at 6 sigma here and a mark
# at 6 sigma there sit at the same distance off the same scale.
FEATURE_TRACK_CEILING = SIGMA_CEILING


@dataclass
class FeaturePoint:
    hour_start: int
    sigma: float | None
    x: float
    y: float


def feature_track(episodes, index: int, mean: float,
                  std: float) -> list[FeaturePoint]:
    """One feature of one source, hour by hour.

    This is the only honest chart the stored data supports for a single
    dimension. A mean and a standard deviation describe a position, not a
    shape, so a curve drawn from them would assert something about this
    tenant's traffic that nothing has ever measured. The value frozen onto
    each hourly episode is a real measurement, and a series of them is a real
    series.

    `sigma` is None wherever it genuinely cannot be computed - no vector, a
    vector too short to line up, or a baseline with no spread. Those are holes
    and the caller must draw them as holes: a hole says the instrument had
    nothing, a zero says the source was normal, and in an hour when it was
    being blocked those are opposite claims.

    `y` is DISTANCE from normal, so a feature four sigma below the baseline
    sits as high as one four sigma above. `sigma` keeps its sign, because the
    table beside the chart has to be able to say which way.
    """
    ordered = sorted(episodes, key=lambda e: e.hour_start)
    span = max(len(ordered) - 1, 1)
    points = []
    for i, e in enumerate(ordered):
        vector = list(e.last_features or [])
        sigma = None
        if std > 0 and index < len(vector):
            sigma = (float(vector[index]) - mean) / std
        # Clamped for DRAWING only. The reading itself is never clipped: a
        # source at 98 sigma is reported at 98 sigma and drawn at the edge.
        magnitude = min(abs(sigma), FEATURE_TRACK_CEILING) if sigma is not None else 0.0
        points.append(FeaturePoint(
            hour_start=e.hour_start,
            sigma=sigma,
            x=round(i * FEATURE_TRACK_WIDTH / span, 2),
            y=round(FEATURE_TRACK_HEIGHT * (1 - magnitude / FEATURE_TRACK_CEILING), 2),
        ))
    return points


def feature_runs(points: list[FeaturePoint]) -> list[list[FeaturePoint]]:
    """Consecutive readings, split on every hole.

    One polyline through the lot would draw a straight line across the hours
    that have no reading, which is precisely the claim the hole exists to
    avoid making.
    """
    runs: list[list[FeaturePoint]] = []
    current: list[FeaturePoint] = []
    for p in points:
        if p.sigma is None:
            if current:
                runs.append(current)
            current = []
        else:
            current.append(p)
    if current:
        runs.append(current)
    return runs


# --- history: the same axis, rotated and stacked ---------------------------

# Twenty-four rows read comfortably at 10 units each; a hundred and sixty
# eight at that height is a very long scroll of the same picture. The grid
# keeps its total height roughly constant and shrinks the row instead.
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
    # The two figures spec 8 asks the parallel table for, beside the hour.
    # Position on an axis is what a screen reader cannot read, so the table is
    # mandatory - and it is a SUMMARY per row, not a matrix: 24 by 13 is 312
    # numbers and nobody wants 312 numbers read aloud.
    past_gate: int = 0
    peak_sigma: float | None = None

    @property
    def label(self) -> str:
        """The hour, as a person says it. Read aloud one row at a time, so a
        raw epoch would be the worst possible thing here."""
        from datetime import datetime, timezone

        return datetime.fromtimestamp(self.hour_start,
                                      tz=timezone.utc).strftime("%d %b %H:00")


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
    """One row per hour, sigma along the row, the gates straight through.

    The deviation chart this replaces plotted one number per hour - the worst
    sigma seen - which answers "was there a spike" and cannot answer "where
    did my traffic sit", the question the gate control is for. Both come out
    of the thirteen bins the hourly row already carries, so this is a second
    reading of a Query the page already makes rather than a second Query.

    Only cells with a count are emitted. That is a requirement and not an
    optimisation: a dense 168-row grid emitting all thirteen bins per row is
    about 22KB of markup describing absence.
    """
    from services.backend.core.tables import TenantHistoryTable

    ordered = sorted(series, key=lambda p: p.hour_start)
    width = SIGMA_CEILING * AXIS_UNITS_PER_SIGMA
    if not ordered:
        return Grid(width=width, height=0, row_height=0, busiest=0)

    row_height = min(GRID_MAX_ROW_HEIGHT, GRID_MAX_HEIGHT / len(ordered))
    edges = TenantHistoryTable.NEAR_BINS
    step = (edges[1] - edges[0]) if len(edges) > 1 else 0.25
    cell_width = step * AXIS_UNITS_PER_SIGMA

    # Identified sources are not in the bins: a source past the gate is stored
    # by address and never folded into the anonymous shape, so without this
    # the grid would simply stop at the gate.
    by_hour: dict[int, list] = {}
    for e in episodes:
        if e.last_z is None:
            continue
        by_hour.setdefault(int(e.hour_start), []).append(e)

    rows: list[GridRow] = []
    busiest = 0
    for i, point in enumerate(ordered):
        hour = int(point.hour_start)
        cells: list[GridCell] = []
        for edge in edges:
            count = int((point.near or {}).get("n%d" % round(edge * 100), 0))
            if not count:
                continue
            busiest = max(busiest, count)
            cells.append(GridCell(x=axis_x(edge), width=cell_width,
                                  count=count, level=0, sigma=edge,
                                  identified=False))
        for e in by_hour.get(hour, []):
            magnitude = abs(e.last_z)
            # Clamped for DRAWING only, exactly as the main axis clamps: the
            # reading itself is reported unclipped beside it.
            cells.append(GridCell(x=axis_x(magnitude), width=2.0, count=1,
                                  level=GRID_LEVELS, sigma=magnitude,
                                  identified=True))
        # None rather than zero where nothing was read: zero sigma is a
        # measurement meaning "exactly normal", and an hour with no telemetry
        # measured nothing at all.
        peak = max((c.sigma for c in cells), default=None)
        rows.append(GridRow(
            hour_start=hour, y=round(i * row_height, 2),
            # An hour whose only record is a block it issued has plainly not
            # lost its feed, whatever the batch counter says.
            live=bool(point.batches) or bool(cells),
            cells=cells,
            past_gate=sum(1 for c in cells if c.identified),
            peak_sigma=peak))

    # Bucketed after the fact, because the busiest cell is not known until
    # every row has been read.
    for row in rows:
        for cell in row.cells:
            if cell.identified:
                continue
            share = cell.count / busiest if busiest else 0
            # Never zero. One near-miss in an otherwise empty hour is exactly
            # the reading this grid exists to show, and level 0 draws nothing.
            cell.level = max(1, math.ceil(share * GRID_LEVELS))

    return Grid(
        width=width,
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
