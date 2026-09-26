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
    # How many whole sigma this band covers. The SVG places the band by `x`
    # and `width`; the LABEL is HTML in a six-column grid, and with no span
    # the three labels took columns one to three and bunched into the left
    # half of a chart whose bands ran the full width.
    cols: int = 1


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
              "TRAFFIC BÌNH THƯỜNG CỦA BẠN", "BÌNH THƯỜNG"),
             (tier1_sigma, tier2_sigma, "c-seg c-seg--limited",
              f"{tier1_sigma:.0f}σ LÀM CHẬM", f"{tier1_sigma:.0f}σ"),
             (tier2_sigma, SIGMA_CEILING, "c-seg c-seg--blocked",
              f"{tier2_sigma:.0f}σ CHẶN", f"{tier2_sigma:.0f}σ")]
    # At least one column each, and never more than the scale has: a gate
    # dragged to the far end would otherwise hand one band every column and
    # push the other two off the grid entirely.
    return [Band(x=axis_x(lo), width=axis_x(hi) - axis_x(lo), css=css,
                 label=label, short=short,
                 cols=max(1, min(int(SIGMA_CEILING), round(hi - lo))))
            for lo, hi, css, label, short in edges]


@dataclass
class DensityBar:
    x: float
    width: float
    height: float     # 0..1, relative to the tallest bar
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

    # No `css` any more. The thirteen bars these described were replaced by
    # one ridge path, and nothing has read the class since; the numbers stay
    # because `density_ridge` shapes the path from them and the screen
    # reader's summary table sums them.
    return [DensityBar(x=axis_x(s), width=axis_x(s + step) - axis_x(s),
                       height=(c / tallest) if tallest else 0.0,
                       sigma=s, count=c)
            for s, c in zip(edges, counts)]


@dataclass
class Mark:
    x: float
    sigma: float
    css: str
    ip: str
    # Where the dot sits vertically. The axis is one dimension of meaning and
    # the second is free, so it is spent on separation: two sources at the
    # same distance would otherwise draw on top of each other and read as
    # one. Deterministic from the address, so a source does not jump between
    # page loads - a dot that moves on refresh looks like a new measurement.
    y: float = 0.0
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
                          ip=ip, y=_mark_y(ip),
                          # `?ip=` with nothing after it arrives as an empty
                          # string, which would otherwise match every row
                          # whose ip is also missing.
                          selected=bool(selected_ip) and ip == selected_ip))
    return marks


# Enough room for a dot and its halo without either touching an edge. The
# axis was 48 units tall when it drew vertical rules, which have no radius.
AXIS_HEIGHT = 96.0
_MARK_BAND = (22.0, 64.0)


def _mark_y(ip: str) -> float:
    """A stable vertical offset for one source.

    Hashed from the address rather than randomised or taken from the loop
    index: random moves the dot on every refresh, which reads as a new
    reading, and the index reshuffles every dot the moment one source is
    allowed through.
    """
    lo, hi = _MARK_BAND
    return round(lo + (sum(ip.encode()) % 97) / 96 * (hi - lo), 2)


def density_ridge(bars: list["DensityBar"], width: float, height: float) -> str:
    """The shape below the gate as one path, not thirteen bars.

    Thirteen separate bars assert that 3.00 and 3.25 sigma are two different
    worlds. They are two readings on one continuous slope, and a ridge is
    what a slope looks like. Returns "" when nothing was measured, so the
    caller draws nothing at all rather than a flat line along the floor -
    which would be a reading, and there is none.
    """
    if not bars or not any(b.count for b in bars):
        return ""
    pts = [f"{round(b.x + b.width / 2, 2)},{round(height * (1 - b.height), 2)}"
           for b in bars]
    return "M0," + str(height) + " L" + " L".join(pts) + f" L{width},{height}"


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
    ridge: str = ""


def build_axis(tier1_sigma: float, tier2_sigma: float,
               bins: dict[str, int], rows: list[dict],
               selected_ip: str | None = None) -> Axis:
    """Everything the macro needs, in one object.

    Its size is constant in the number of sources below the gate: those
    become thirteen density bars whatever their count, and only the sources
    above the gate - the mitigation list, already bounded - become individual
    marks.
    """
    width = SIGMA_CEILING * AXIS_UNITS_PER_SIGMA
    bars = density_bars(bins)
    return Axis(
        width=width,
        height=AXIS_HEIGHT,
        bands=axis_bands(tier1_sigma, tier2_sigma),
        density=bars,
        ridge=density_ridge(bars, width, AXIS_HEIGHT),
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


# --- history: the same axis, rotated and stacked ---------------------------

# Twenty-four rows read comfortably at 10 units each; a hundred and sixty
# eight at that height is a very long scroll of the same picture. The grid
# keeps its total height roughly constant and shrinks the row instead.
GRID_MAX_HEIGHT = 260.0
GRID_MAX_ROW_HEIGHT = 10.0
GRID_LEVELS = 4


