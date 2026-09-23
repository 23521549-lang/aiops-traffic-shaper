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


# --- the sigma strip ------------------------------------------------------

@dataclass
class SigmaStrip:
    """The static legend, upgraded into an instrument.

    It renders whether or not anything is happening, because a customer with
    nothing blocked should still learn what the product will do before it
    does it. When something IS active, a marker shows where.
    """
    width: float = 320
    height: float = 34
    segments: list[tuple[float, float, str, str]] = field(default_factory=list)
    ticks: list[tuple[float, str]] = field(default_factory=list)
    marker: tuple[float, str] | None = None

    MAX = SIGMA_CEILING


def sigma_strip(active_sigma: float | None = None) -> SigmaStrip:
    strip = SigmaStrip()

    def x(sigma: float) -> float:
        return strip.width * min(abs(sigma), SIGMA_CEILING) / SIGMA_CEILING

    bounds = [(0.0, TIER1_SIGMA, "c-seg c-seg--normal", "your normal traffic"),
              (TIER1_SIGMA, TIER2_SIGMA, "c-seg c-seg--limited",
               f"{TIER1_SIGMA:.0f}σ slowed"),
              (TIER2_SIGMA, SIGMA_CEILING, "c-seg c-seg--blocked",
               f"{TIER2_SIGMA:.0f}σ blocked")]
    for lo, hi, css, text in bounds:
        strip.segments.append((x(lo), x(hi) - x(lo), css, text))

    strip.ticks = [(x(v), f"{v:.0f}σ") for v in (3.0, TIER1_SIGMA, TIER2_SIGMA)]

    if active_sigma is not None:
        magnitude = abs(active_sigma)
        strip.marker = (x(magnitude), f"{magnitude:.1f}σ")
    return strip


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
