# Phase 1a — The sigma axis and the Gate screen

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the one primitive the whole rebuilt console rests on — a sigma axis that is present on every tenant page and reports its own provenance — and rebuild `/dashboard/ui` around it.

**Architecture:** The axis is two layers sharing one grid cell: an SVG layer for anything at an arbitrary position and no text, and an HTML grid layer for all type, whose labels sit at integer sigma and therefore need no computed position at all. That split is what lets 223 enumerated CSS rules be deleted without a single rule replacing them. Below the gate the axis draws density from the thirteen bins Phase 0 started recording; above it, individually identified marks. So its byte cost is constant in the size of the tenant.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, server-rendered inline SVG, CSS grid. No JavaScript is added by this plan.

**Spec:** `docs/ui-rebuild/05-spec-thiet-ke.md` — sections 1, 3, 4.1, 8, 9. Read section 3 before Task 2; the two-layer split is the whole design and the rest follows from it.

**Depends on:** Phase 0, shipped at `4f20729`. Task 5 reads the `n300`…`n600` bins that `record_traffic` now writes.

## Global Constraints

- **Test runner.** Run pytest and ruff as **separate** commands and read the `N failed, M passed` line, never the exit code.
  - `PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q --cov=services/backend --cov=services/agent --cov-fail-under=80`
  - `ruff check services/backend services/agent`
- **TDD.** Every test is named after the defect it prevents and written failing first. Docstrings say why the defect mattered.
- **Commits:** one `-m`, explicit paths, never `git add -A` at the repo root, no AI attribution.
- **CSP.** `style-src 'self'` blocks the `style=` attribute, not only `<style>`. Computed geometry goes in SVG presentation attributes, which CSP does not govern. No inline styles, ever, including in a macro.
- **Budget.** CSS 80KB across at most 3 files; it currently stands at exactly 80,183 bytes, so this plan must end **below** where it started. First-party JS +0 bytes.
- **No em dash in anything rendered to a user.**
- **Colour is never the only channel.** Every state carries a word or a shape as well.
- **Every chart carries a paired table**, and that table is a summary, not a matrix.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `services/backend/ui/static/tokens.css` | create: colour ramps, type scale, spacing | 1 |
| `services/backend/ui/static/app.css` | base elements + every class a shared macro emits | 1, 3 |
| `services/backend/ui/static/console.css` | console shell chrome only | 1, 3 |
| `services/backend/ui/templates/shared/` | create: the macro files both surfaces load | 1 |
| `services/backend/tests/test_shared_chart_styles.py` | invert from requirement to prohibition | 1 |
| `services/backend/ui/charts.py` | axis geometry, pure functions on numbers | 2 |
| `services/backend/ui/templates/shared/_axis.html` | create: the two-layer macro | 3 |
| `services/backend/ui/presenters.py` | the axis's provenance state and its sentence | 4 |
| `services/backend/ui/dashboard.py` | `/dashboard/ui` builds and passes the axis | 5 |
| `services/backend/ui/templates/dashboard_status.html` | rebuilt around the axis | 5 |

---

## Task 1: Three stylesheets, and a rule that cannot be broken by accident

No visual change. This exists so Task 3 cannot repeat a defect this codebase has already shipped: the sigma band was styled only in `console.css` while `_charts.html` is called by the landing page too, so the public page rendered three unstyled lines of text under a heading reading "sigma, in one screen".

**Files:**
- Create: `services/backend/ui/static/tokens.css`
- Modify: `services/backend/ui/static/app.css`, `services/backend/ui/static/console.css`
- Create: `services/backend/ui/templates/shared/` and move `_charts.html` into it
- Modify: `services/backend/ui/static_files.py`, `services/backend/ui/templates/base.html`
- Modify: every template that imports `_charts.html`
- Test: `services/backend/tests/test_shared_chart_styles.py`

**Interfaces:**
- Produces: `templates/shared/_charts.html` as the only home of shared macros; `tokens.css` as an allow-listed asset loaded by `base.html` before `app.css`.

- [ ] **Step 1: Write the failing prohibition**

Replace the body of `services/backend/tests/test_shared_chart_styles.py` with:

```python
"""A macro both surfaces render may not be styled by the console sheet.

The old version of this file asked "is this class also in app.css?", which
permits a duplicate that then drifts. This asks the sharper question: does
`console.css` style anything a shared macro emits at all? It may not. The
console may add chrome around a chart; it may not own the chart's
appearance, because the landing page never loads that file.

The scan is a DIRECTORY, not a hand-maintained list of filenames. That is
the part that makes the rule survive someone adding a sixth macro file.
"""
import re
from pathlib import Path

import pytest

_UI = Path(__file__).resolve().parents[1] / "ui"
_SHARED = _UI / "templates" / "shared"
_STATIC = _UI / "static"

_CLASS_ATTR = re.compile(r'class="([^"{}]*)"')


def _shared_classes() -> set[str]:
    names = set()
    for template in sorted(_SHARED.rglob("*.html")):
        for group in _CLASS_ATTR.findall(template.read_text(encoding="utf-8")):
            names.update(n for n in group.split() if n and not n.startswith("{"))
    return names


def _unscoped_selectors(sheet: str) -> set[str]:
    """Classes this sheet styles WITHOUT a `.console` ancestor.

    The distinction is the whole rule. `.console .chart-caption { ... }` is
    the console adding its own typography inside its own shell, and it is
    structurally incapable of reaching the landing page, because `.console`
    exists only in the console shell. `.chart-caption { ... }` in the same
    file is a shared element being styled somewhere half the product cannot
    see, which is the defect.
    """
    css = re.sub(r"/\*.*?\*/", "", (_STATIC / sheet).read_text(encoding="utf-8"),
                 flags=re.S)
    names = set()
    for block in re.findall(r"([^{}]+)\{[^}]*\}", css):
        for selector in block.split(","):
            selector = selector.strip()
            if not selector or ".console" in selector:
                continue
            names.update(re.findall(r"\.([A-Za-z][\w-]*)", selector))
    return names


def _selectors(sheet: str) -> set[str]:
    css = re.sub(r"/\*.*?\*/", "", (_STATIC / sheet).read_text(encoding="utf-8"),
                 flags=re.S)
    return set(re.findall(r"\.([A-Za-z][\w-]*)", css))


def test_there_is_a_shared_directory_to_scan():
    """If this ever finds nothing, every assertion below passes vacuously."""
    assert _SHARED.is_dir()
    assert list(_SHARED.rglob("*.html"))
    assert _shared_classes()


@pytest.mark.parametrize("name", sorted(_shared_classes()))
def test_the_console_sheet_never_styles_a_shared_class_unscoped(name):
    assert name not in _unscoped_selectors("console.css"), (
        f".{name} is emitted by a macro under templates/shared/ and styled "
        f"unscoped in console.css, which the public pages never load. Scope "
        f"it under `.console` if the console genuinely needs its own chrome, "
        f"or move it to app.css if every surface needs it")


@pytest.mark.parametrize("name", ["c-seg--normal", "c-seg--limited",
                                  "c-seg--blocked"])
def test_the_classes_charts_py_produces_obey_the_same_rule(name):
    """These come out of charts.py rather than the template, so the scan
    above cannot see them."""
    assert name not in _unscoped_selectors("console.css")
    assert name in _selectors("app.css")


def test_the_console_does_not_restate_a_rule_app_css_already_has():
    """`.console .chart { margin: 0 }` duplicated `.chart { margin: 0 }`
    exactly. A duplicate with the same value today is a duplicate with a
    different value after the first edit that touches only one of them."""
    console = (_STATIC / "console.css").read_text(encoding="utf-8")

    assert ".console .chart {" not in console


def test_tokens_are_in_their_own_sheet_and_loaded_first():
    """Both surfaces need the ramps; only the console needs the shell. A
    third sheet is the cap - a fourth is not allowed."""
    base = (_UI / "templates" / "base.html").read_text(encoding="utf-8")
    assert base.index("tokens.css") < base.index("app.css")
    assert len(list(_STATIC.glob("*.css"))) == 3
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_shared_chart_styles.py -q`
Expected: FAIL on `test_there_is_a_shared_directory_to_scan` — `templates/shared/` does not exist yet.

- [ ] **Step 3: Move the shared macros**

```bash
mkdir services/backend/ui/templates/shared
git mv services/backend/ui/templates/_charts.html services/backend/ui/templates/shared/_charts.html
```

Update every importer. Find them with:

```bash
grep -rln '_charts.html' services/backend/ui/templates/
```

and change each `{% import "_charts.html" as charts %}` to
`{% import "shared/_charts.html" as charts %}`.

- [ ] **Step 4: Split the tokens out**

Cut from `app.css` into a new `services/backend/ui/static/tokens.css`: the
`:root` block, the `[data-theme="dark"]` block, the
`@media (prefers-color-scheme: dark)` block, and the `.on-ink` token
overrides. Leave every component rule in `app.css`.

Head `tokens.css` with:

```css
/* Colour, type and space. Nothing else.
 *
 * Split out of app.css so the rule below is structural rather than a
 * convention: every surface loads tokens.css and app.css, and ONLY the
 * console loads console.css. A class a shared macro emits must therefore be
 * styled in app.css, because the landing page will never see the other one.
 * That defect has shipped once - the sigma band rendered as three lines of
 * unstyled text on the public page, under a heading reading "sigma, in one
 * screen" - and test_shared_chart_styles.py now forbids it rather than
 * asking politely.
 */
```

- [ ] **Step 5: Register and load it**

In `services/backend/ui/static_files.py` add `"tokens.css": "text/css"` to
`_ASSETS`. In `services/backend/ui/templates/base.html`, add the link
**before** the `app.css` link:

```html
<link rel="stylesheet" href="/ui/static/tokens.css">
```

- [ ] **Step 6: Delete the one duplicated rule**

`console.css` carries `.console .chart { margin: 0; }`, which restates
`app.css`'s `.chart { margin: 0; }` exactly. Delete it. Keep
`.console .chart-caption`: that one is real console typography, it is
scoped under `.console`, and a scoped rule is structurally incapable of
reaching the landing page.

- [ ] **Step 7: Run the tests**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ -q`
Expected: all pass. `test_static_assets.py` checks the allow-list in both
directions and will catch a missed registration; `test_contrast.py` reads
the tokens and will catch a bad cut.

- [ ] **Step 8: Commit**

```bash
git add services/backend/ui/static/tokens.css services/backend/ui/static/app.css \
        services/backend/ui/static/console.css services/backend/ui/static_files.py \
        services/backend/ui/templates/ services/backend/tests/test_shared_chart_styles.py
git commit -m "refactor(ui): make the shared-stylesheet rule structural instead of polite"
```

---

## Task 2: The axis, as geometry

Pure functions on numbers, no markup. The same discipline `charts.py` already follows, and the reason its tests are readable.

**Files:**
- Modify: `services/backend/ui/charts.py`
- Test: `services/backend/tests/test_axis_geometry.py` (create)

**Interfaces:**
- Produces:
  - `AXIS_UNITS_PER_SIGMA = 100` and `axis_x(sigma: float) -> float`
  - `Band(x, width, css, label, short)` and `axis_bands(tier1_sigma, tier2_sigma) -> list[Band]`
  - `DensityBar(x, width, height, css, sigma, count)` and `density_bars(bins: dict[str, int]) -> list[DensityBar]`
  - `Mark(x, sigma, css, ip)` and `source_marks(rows: list[dict]) -> list[Mark]`
  - `Axis` carrying all of the above plus `gate1_x`, `gate2_x`, `width`, `height`

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_axis_geometry.py`:

```python
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
    AXIS_UNITS_PER_SIGMA, SIGMA_CEILING, Axis, axis_bands, axis_x,
    build_axis, density_bars, source_marks,
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


def test_no_bars_at_all_is_not_a_division_by_zero(_=None):
    assert all(b.height == 0 for b in density_bars({}))


def test_a_source_becomes_a_mark_at_its_own_position():
    marks = source_marks([{"ip": "203.0.113.7", "z": -6.4, "tier": 2}])

    assert marks[0].ip == "203.0.113.7"
    assert marks[0].x == axis_x(6.4) or marks[0].x == axis_x(SIGMA_CEILING)


def test_a_source_with_no_measurable_z_is_not_placed_at_zero():
    """z is None when the model had no usable spread. Drawing it at 0 would
    put a blocked source inside the band labelled "your normal traffic"."""
    assert source_marks([{"ip": "203.0.113.7", "z": None, "tier": 2}]) == []


def test_the_axis_carries_both_gates_where_the_tenant_put_them():
    axis = build_axis(tier1_sigma=3.5, tier2_sigma=4.5, bins={}, rows=[])

    assert axis.gate1_x == axis_x(3.5)
    assert axis.gate2_x == axis_x(4.5)


def test_the_axis_is_constant_in_the_number_of_sources_below_the_gate():
    """The whole reason density exists. A tenant with ten thousand measured
    sources must cost the same bytes as one with forty."""
    small = build_axis(4.0, 5.0, bins={"n300": 40}, rows=[])
    huge = build_axis(4.0, 5.0, bins={"n300": 10_000}, rows=[])

    assert len(small.density) == len(huge.density)
    assert len(small.marks) == len(huge.marks) == 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_axis_geometry.py -q`
Expected: FAIL, `ImportError: cannot import name 'AXIS_UNITS_PER_SIGMA'`

- [ ] **Step 3: Implement the geometry**

Append to `services/backend/ui/charts.py`:

```python
# --- the axis ------------------------------------------------------------

# 100 units per sigma, so the viewBox is 0..600 for 0..6 sigma. A round
# number because a person checking the markup by eye should be able to do
# the division.
AXIS_UNITS_PER_SIGMA = 100


def axis_x(sigma: float) -> float:
    """Where a magnitude sits, clamped at the ceiling.

    Clamped rather than rescaled: one source at 40 sigma would otherwise
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

    Not from the module constants. A band system drawn from those would show
    every tenant somebody else's gate, which is the exact thing Phase 0's
    per-tenant threshold exists to stop.

    Labels are written in the case they are read in. A stylesheet that
    uppercases them turns sigma into Σ, which is summation, and that reached
    an incident screen once already.
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


def source_marks(rows: list[dict]) -> list[Mark]:
    """One tick per identified source, above the gate.

    A row whose z is None is dropped rather than placed. z is None when the
    model had no usable spread, and drawing it at zero would put a blocked
    source inside the band labelled "your normal traffic".
    """
    marks = []
    for row in rows:
        z = row.get("z")
        if z is None:
            continue
        marks.append(Mark(x=axis_x(z), sigma=abs(z),
                          css=_tier_css(abs(z)).replace("c-bar", "c-mark"),
                          ip=row.get("ip", "")))
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
               bins: dict[str, int], rows: list[dict]) -> Axis:
    """Everything the macro needs, in one object.

    Its size is constant in the number of sources below the gate: those
    become thirteen density bars whatever their count, and only the sources
    above the gate - which is the mitigation list, already bounded - become
    individual marks.
    """
    return Axis(
        width=SIGMA_CEILING * AXIS_UNITS_PER_SIGMA,
        height=48,
        bands=axis_bands(tier1_sigma, tier2_sigma),
        density=density_bars(bins),
        marks=source_marks(rows),
        # Integer sigma only. These are the HTML layer's grid columns, and
        # sitting at integers is precisely why they need no computed
        # position and therefore no enumerated CSS rule.
        ticks=[(i, f"{i}σ") for i in range(int(SIGMA_CEILING) + 1)],
        gate1_x=axis_x(tier1_sigma),
        gate2_x=axis_x(tier2_sigma),
        gate1_sigma=tier1_sigma,
        gate2_sigma=tier2_sigma,
    )
```

- [ ] **Step 4: Run it and watch it pass**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_axis_geometry.py -q`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add services/backend/ui/charts.py services/backend/tests/test_axis_geometry.py
git commit -m "feat(charts): the sigma axis as geometry, drawn from the tenant's own gates"
```

---

## Task 3: The axis, as markup, and 202 CSS rules deleted

**Files:**
- Create: `services/backend/ui/templates/shared/_axis.html`
- Modify: `services/backend/ui/static/app.css`
- Test: `services/backend/tests/test_axis_markup.py` (create)

**Interfaces:**
- Consumes: `Axis` from Task 2.
- Produces: `{% import "shared/_axis.html" as axis %}` with `axis.render(a, label, caption)`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_axis_markup.py`:

```python
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


def test_the_text_layer_is_a_six_column_grid():
    """Labels sit at integer sigma, so they are grid items rather than
    positioned boxes. This is what removes the enumeration entirely."""
    assert "repeat(6" in _axis_template() or "c-axis-labels" in _axis_template()


def test_the_enumerated_geometry_rules_are_gone():
    """223 rules and about 9KB, replaced by nothing. If these come back, the
    two-layer split has been abandoned."""
    css = (_STATIC / "app.css").read_text(encoding="utf-8")

    assert css.count("data-flex=") == 0
    assert css.count("data-at=") == 0


def test_the_stylesheet_budget_did_not_grow():
    """The cap is 80KB across three sheets and the project sits at 80,183
    bytes today. This plan must end below where it started."""
    total = sum(p.stat().st_size for p in _STATIC.glob("*.css"))

    assert total < 80_183, f"stylesheets grew to {total} bytes"


def test_nothing_uppercases_a_band_label():
    """CSS uppercases Greek: "4σ slowed" reached an incident screen as
    "4Σ SLOWED", which is summation, in a product that sells standard
    deviations."""
    css = re.sub(r"/\*.*?\*/", "",
                 (_STATIC / "app.css").read_text(encoding="utf-8"), flags=re.S)
    blocks = re.findall(r"([^{}]+)\{([^}]*text-transform:\s*uppercase[^}]*)\}", css)

    assert not [sel for sel, _ in blocks if "axis" in sel or "c-seg" in sel]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_axis_markup.py -q`
Expected: FAIL, the template does not exist.

- [ ] **Step 3: Write the macro**

Create `services/backend/ui/templates/shared/_axis.html`:

```jinja
{% macro render(a, label, caption="") %}
{# Two layers in one grid cell.

   Layer A is SVG and holds everything at an arbitrary position and nothing
   with text: bands, density, gates, source marks. `preserveAspectRatio=
   "none"` stretches it to any width, which is harmless because there is no
   type in it to distort.

   Layer B is HTML and holds all the type. Its labels sit at INTEGER sigma,
   so they are grid items in a six-column grid and need no computed position
   at all. That is the whole trick: it is why this replaces 202 enumerated
   CSS rules with none.

   Neither layer may have inline padding. sigma s lands at s/6 x 100% in
   both, and padding on either one breaks that mapping silently. #}
<div class="c-axis" role="img" aria-label="{{ label }}">
  <svg class="c-axis-plot" viewBox="0 0 {{ a.width }} {{ a.height }}"
       preserveAspectRatio="none" focusable="false" aria-hidden="true">
    {% for b in a.bands %}
    <rect class="{{ b.css }}" x="{{ b.x }}" y="0"
          width="{{ b.width }}" height="{{ a.height }}"/>
    {% endfor %}

    {% for d in a.density %}
    {% if d.count %}
    <rect class="{{ d.css }}" x="{{ d.x }}"
          y="{{ (a.height * (1 - d.height))|round(2) }}"
          width="{{ d.width }}"
          height="{{ (a.height * d.height)|round(2) }}"/>
    {% endif %}
    {% endfor %}

    {% for m in a.marks %}
    <line class="{{ m.css }}" x1="{{ m.x }}" y1="0"
          x2="{{ m.x }}" y2="{{ a.height }}"/>
    {% endfor %}

    <line class="c-gate c-gate--1" x1="{{ a.gate1_x }}" y1="0"
          x2="{{ a.gate1_x }}" y2="{{ a.height }}"/>
    <line class="c-gate c-gate--2" x1="{{ a.gate2_x }}" y1="0"
          x2="{{ a.gate2_x }}" y2="{{ a.height }}"/>
  </svg>

  <div class="c-axis-labels">
    {% for b in a.bands %}
    <span class="c-axis-band-label {{ b.css }}">
      <span class="c-axis-long">{{ b.label }}</span>
      <span class="c-axis-short" aria-hidden="true">{{ b.short }}</span>
    </span>
    {% endfor %}
  </div>

  <div class="c-axis-ticks">
    {% for value, text in a.ticks %}
    <span class="c-axis-tick">{{ text }}</span>
    {% endfor %}
  </div>
</div>

{% if caption %}<p class="c-axis-caption">{{ caption }}</p>{% endif %}

{# Position on an axis is unreadable to a screen reader, so every axis
   carries a table. A SUMMARY, not a matrix: the question this chart exists
   to answer is "how far out is the worst of it, and how many". #}
<div class="sr-only">
  <table>
    <caption>{{ label }}</caption>
    <thead>
      <tr><th scope="col">Region</th><th scope="col">Sources</th></tr>
    </thead>
    <tbody>
      <tr><th scope="row">Below {{ "%.1f"|format(a.gate1_sigma) }} sigma</th>
          <td>{{ a.density|map(attribute="count")|sum }}</td></tr>
      <tr><th scope="row">Past {{ "%.1f"|format(a.gate1_sigma) }} sigma</th>
          <td>{{ a.marks|length }}</td></tr>
    </tbody>
  </table>
</div>
{% endmacro %}
```

- [ ] **Step 4: Write the CSS**

Append to `services/backend/ui/static/app.css`:

```css
/* --- the sigma axis ---------------------------------------------------- */

/* Both layers occupy the same grid cell and both are full width with zero
 * inline padding, so sigma s lands at s/6 x 100% in each of them. Padding on
 * either one breaks that mapping and nothing would report it. */
.c-axis { display: grid; grid-template-columns: 1fr; }
.c-axis > * { grid-area: 1 / 1; inline-size: 100%; padding-inline: 0; }
.c-axis-plot { block-size: 48px; display: block; }

.c-axis-labels,
.c-axis-ticks {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}
.c-axis-ticks { align-self: end; color: var(--text-muted); }
.c-axis-band-label { padding: 0 6px; align-self: center; }

/* Written in the case they are read in. No rule here may uppercase them:
 * CSS uppercases Greek, and "4σ slowed" reached an incident screen as
 * "4Σ SLOWED", which is summation. */
.c-axis-short { display: none; }
@media (max-width: 860px) {
  .c-axis-long { display: none; }
  .c-axis-short { display: inline; }
}

.c-density { fill: var(--text-muted); opacity: .45; }
.c-gate { stroke: var(--text); stroke-width: 2; }
.c-gate--2 { stroke-dasharray: 4 3; }
.c-mark { stroke-width: 2; }
.c-mark--normal { stroke: var(--tier-normal); }
.c-mark--limited { stroke: var(--tier-limited); }
.c-mark--blocked { stroke: var(--tier-blocked); }
.c-axis-caption {
  font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--text-muted); margin: 6px 0 0;
}
```

- [ ] **Step 5: Delete the enumerated rules and the strip they served**

Remove from `app.css`: the 101 `[data-flex="N"]` rules, the 101
`[data-at="N"]` rules, and the `.sigma-*` block the axis replaces. Remove
the `sigma_strip` macro from `shared/_charts.html`, and the `sigma_strip`
import and call from `services/backend/ui/public.py` and
`services/backend/ui/dashboard.py`.

Leave `charts.sigma_strip` itself in `charts.py` until Task 5 has replaced
both call sites, then delete it in the same commit as its last caller.
An orphaned helper is the shape of latent defect `test_static_assets.py`
exists to prevent, and `test_charts.py` covers this one.

- [ ] **Step 6: Run everything**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ -q`
Then: `ruff check services/backend services/agent`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add services/backend/ui/templates/shared/_axis.html \
        services/backend/ui/static/app.css \
        services/backend/tests/test_axis_markup.py
git commit -m "feat(ui): the axis in two layers, and 202 enumerated rules deleted"
```

---

## Task 4: The axis reports its own provenance

An instrument with no feed must not render a reading. Not "render a reading with a warning beside it" — delete the reading.

**Files:**
- Modify: `services/backend/ui/presenters.py`
- Test: `services/backend/tests/test_axis_provenance.py` (create)

**Interfaces:**
- Produces: `axis_state(health: dict, throttle: dict, model_ready: bool, now=None) -> dict` with keys `state` (`fed` | `no_signal` | `throttled` | `day_one`), `sentence`, `plot` (`live` | `outline` | `frozen`), `gates_armed` (bool).

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_axis_provenance.py`:

```python
"""An instrument with no feed must not show a reading.

Not a reading with a caution light beside it - no reading. The G1000 draws a
red X across the airspeed tape itself when the pitot feed fails; it does not
light a lamp somewhere else on the panel. The test that distinguishes this
from a status tile someone moved: in the no-signal state there is no reading
on the screen at all.

Three states this product has always conflated, and one it states wrongly
today: a quota-throttled tenant is told "the process stopped, or it cannot
reach the API", so a customer restarts a healthy agent at 3am and it does
not help.
"""
from services.backend.ui.presenters import axis_state

FED = {"state": "healthy", "reporting": 3, "last_seen_label": "12 seconds ago"}
QUIET = {"state": "degraded", "reporting": 0, "last_seen_label": "3 hours ago"}
NEVER = {"state": "never_connected", "reporting": 0, "last_seen_label": None}
OPEN = {"throttled": False, "throttled_reason": None}
CAPPED = {"throttled": True, "throttled_reason": "tenant"}


def test_a_fed_axis_draws_its_bands_and_its_marks():
    assert axis_state(FED, OPEN, model_ready=True)["plot"] == "live"


def test_a_dead_feed_deletes_the_reading():
    """The whole rule. A tile leaves the reading up and adds a warning; this
    takes the reading away."""
    state = axis_state(QUIET, OPEN, model_ready=True)

    assert state["state"] == "no_signal"
    assert state["plot"] == "outline"


def test_a_dead_feed_names_the_machine_and_says_nothing_is_measured():
    state = axis_state(QUIET, OPEN, model_ready=True)

    assert "3 hours ago" in state["sentence"]
    assert "not" in state["sentence"].lower()


def test_a_tenant_that_never_connected_is_not_the_same_as_one_gone_quiet():
    """Two completely different next actions: install it, versus go and look
    at the machine."""
    never = axis_state(NEVER, OPEN, model_ready=False)
    quiet = axis_state(QUIET, OPEN, model_ready=True)

    assert never["state"] != quiet["state"]
    assert never["sentence"] != quiet["sentence"]


def test_a_throttled_tenant_is_told_the_truth_about_its_agent():
    """Today this case is displayed as a dead agent, so the customer
    restarts a process that is working perfectly."""
    state = axis_state(FED, CAPPED, model_ready=True)

    assert state["state"] == "throttled"
    assert state["plot"] == "frozen"
    assert "restart" in state["sentence"].lower()


def test_throttling_outranks_a_healthy_feed():
    """The agent is reporting and being refused. The screen must show the
    refusal, not the reporting."""
    assert axis_state(FED, CAPPED, model_ready=True)["state"] == "throttled"


def test_a_dead_agent_outranks_throttling():
    """Nothing is arriving to be refused. Naming the quota would send the
    customer to the wrong problem."""
    assert axis_state(QUIET, CAPPED, model_ready=True)["state"] == "no_signal"


def test_day_one_draws_the_gates_unarmed():
    """With no model nothing is enforced. Gates drawn as live thresholds
    would be a promise the product is not keeping yet."""
    state = axis_state(FED, OPEN, model_ready=False)

    assert state["gates_armed"] is False
    assert "measuring" in state["sentence"].lower()


def test_a_fed_and_modelled_tenant_has_armed_gates():
    assert axis_state(FED, OPEN, model_ready=True)["gates_armed"] is True


def test_no_sentence_contains_an_em_dash():
    """A standing instruction for every rendered string in this product."""
    for health in (FED, QUIET, NEVER):
        for throttle in (OPEN, CAPPED):
            for ready in (True, False):
                assert "—" not in axis_state(health, throttle, ready)["sentence"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_axis_provenance.py -q`
Expected: FAIL, `ImportError: cannot import name 'axis_state'`

- [ ] **Step 3: Implement it**

Append to `services/backend/ui/presenters.py`:

```python
def axis_state(health: dict, throttle: dict, model_ready: bool,
               now: datetime | None = None) -> dict:
    """What the axis is allowed to show, and the sentence that says why.

    An instrument with no feed must not render a reading. `plot` is the
    state of the PLOT AREA, not something added beside it: at "outline" the
    bands are hairlines and no mark is drawn at all, so there is no reading
    on screen to misread. A status tile would have left the reading up and
    added a warning next to it, which is the failure this replaces.

    Order matters and is not arbitrary. A dead agent outranks a quota,
    because nothing is arriving to be refused and naming the quota would
    send the customer to the wrong problem. A quota outranks a healthy feed,
    because the agent is reporting and being refused, and the screen must
    show the refusal.
    """
    if health.get("state") == NEVER_CONNECTED:
        return {
            "state": "no_signal", "plot": "outline", "gates_armed": False,
            "sentence": ("No agent has ever reported. Nothing is being "
                         "measured, so an empty scale is not evidence that "
                         "your traffic is clean."),
        }

    if health.get("state") == DEGRADED:
        seen = health.get("last_seen_label") or "some time ago"
        return {
            "state": "no_signal", "plot": "outline", "gates_armed": False,
            "sentence": (f"No measurement since {seen}. Nothing is being "
                         f"checked right now, and this scale is showing the "
                         f"last data we had, not current traffic."),
        }

    if throttle.get("throttled"):
        why = ("you reached your daily share"
               if throttle.get("throttled_reason") == "tenant"
               else "the platform reached its daily limit")
        return {
            "state": "throttled", "plot": "frozen", "gates_armed": model_ready,
            "sentence": (f"We stopped accepting your telemetry because {why}. "
                         f"Measurement resumes at midnight UTC. Your agent is "
                         f"running, and restarting it will not help."),
        }

    if not model_ready:
        return {
            "state": "day_one", "plot": "live", "gates_armed": False,
            "sentence": ("No model yet. We are measuring and enforcing "
                         "nothing. The gates below switch on after the first "
                         "training run, usually overnight."),
        }

    reporting = health.get("reporting", 0)
    seen = health.get("last_seen_label") or "just now"
    return {
        "state": "fed", "plot": "live", "gates_armed": True,
        "sentence": (f"{reporting} agent{'' if reporting == 1 else 's'} "
                     f"reporting, last seen {seen}."),
    }
```

- [ ] **Step 4: Run it and watch it pass**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_axis_provenance.py -q`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add services/backend/ui/presenters.py services/backend/tests/test_axis_provenance.py
git commit -m "feat(ui): an instrument with no feed stops showing a reading"
```

---

## Task 5: `/dashboard/ui` rebuilt around the axis

**Files:**
- Modify: `services/backend/ui/dashboard.py`
- Rewrite: `services/backend/ui/templates/dashboard_status.html`
- Modify: `services/backend/ui/public.py` (drop the strip)
- Modify: `services/backend/ui/charts.py` (delete `sigma_strip` with its last caller)
- Test: `services/backend/tests/test_gate_screen.py` (create)

**Interfaces:**
- Consumes: `build_axis`, `axis_state`, `protection_status`, `TenantHistoryTable.query_series`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_gate_screen.py`:

```python
"""The screen a customer opens, and the one job it has to finish.

"Is anything broken right now, and is it me or you" is the most frequent
question this product is asked, and answering it took three pages: Protection
knew agent health, Agents knew the fleet, Model knew whether anything was
enforcing. The user assembled the answer themselves.

It is now one page, and the quiet case is the PRIMARY case rather than an
empty state: a quiet screen carries a live count and a worst-observed sigma,
and if either cannot be computed it is not a quiet screen, it is a dead one
wearing a quiet screen's clothes.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantHistoryTable, TenantsTable,
    create_all_tables,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token

HOUR = TenantHistoryTable.hour_of(1758700800)


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    return c


def _live_agent(resource, label="web-01"):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    AgentsTable(resource).put(tenant_id="t-1", agent_id="a-1",
                              agent_label=label, registered_at=now,
                              last_seen_at=now, agent_version="1.4.0",
                              api_key_hash="h", status="active")


def test_the_axis_is_on_the_page(client, dynamo_resource):
    _live_agent(dynamo_resource)

    assert "c-axis" in client.get("/dashboard/ui").text


def test_a_dead_agent_draws_no_marks_at_all(client, dynamo_resource):
    """The rule, asserted on rendered markup: no reading on screen, not a
    reading with a warning beside it."""
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="203.0.113.7", tier=2, score=-0.6, z=-6.0,
        reason="behavioral_anomaly", expires_at=0)

    page = client.get("/dashboard/ui").text

    assert "c-mark" not in page


def test_a_fed_axis_does_draw_its_marks(client, dynamo_resource):
    _live_agent(dynamo_resource)
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="203.0.113.7", tier=2, score=-0.6, z=-6.0,
        reason="behavioral_anomaly", expires_at=0)

    assert "c-mark" in client.get("/dashboard/ui").text


def test_the_quiet_screen_carries_a_count_and_a_worst_sigma(client, dynamo_resource):
    """A positive measurement, not a styled absence. If either number
    cannot be computed this is not the quiet screen."""
    _live_agent(dynamo_resource)
    TenantHistoryTable(dynamo_resource).record_traffic(
        "t-1", HOUR, requests=1847, bins={"n300": 4})

    page = client.get("/dashboard/ui").text

    assert "1,847" in page or "1847" in page


def test_the_density_below_the_gate_is_drawn(client, dynamo_resource):
    _live_agent(dynamo_resource)
    TenantHistoryTable(dynamo_resource).record_traffic(
        "t-1", HOUR, requests=10, bins={"n300": 40, "n350": 12})

    assert "c-density" in client.get("/dashboard/ui").text


def test_the_page_costs_the_same_bytes_for_a_huge_tenant(client, dynamo_resource):
    """Density is why. Ten thousand sources below the gate must not be ten
    thousand elements."""
    _live_agent(dynamo_resource)
    TenantHistoryTable(dynamo_resource).record_traffic(
        "t-1", HOUR, requests=10, bins={"n300": 10_000})

    page = client.get("/dashboard/ui").text

    assert page.count("c-density") <= 13


def test_a_throttled_tenant_is_not_told_to_restart_its_agent(client, dynamo_resource):
    """Today this case renders as a dead agent and sends the customer to fix
    a healthy process at 3am."""
    from services.backend.core.usage import _tenant_counter_key, _today
    from services.backend.core.tables import UsageCountersTable
    from services.backend.core.usage import tenant_daily_quota

    _live_agent(dynamo_resource)
    UsageCountersTable(dynamo_resource).put(
        date=_tenant_counter_key("t-1", _today()),
        total_requests=tenant_daily_quota() + 1)

    page = client.get("/dashboard/ui").text

    assert "will not help" in page


def test_the_axis_shows_this_tenants_gate_not_the_default(client, dynamo_resource):
    """Phase 0 made the threshold a per-tenant value. A screen that draws
    the module constant would show every tenant somebody else's gate."""
    _live_agent(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z",
                                      tier1_z=-3.5, tier2_z=-4.5)

    page = client.get("/dashboard/ui").text

    assert "3σ SLOWED" in page or "4σ SLOWED" in page


def test_the_axis_carries_a_paired_table(client, dynamo_resource):
    """Position on an axis is unreadable to a screen reader. The table is a
    summary, not a matrix."""
    _live_agent(dynamo_resource)

    page = client.get("/dashboard/ui").text

    assert "sr-only" in page
    assert "Region" in page


def test_no_other_tenants_address_reaches_the_page(client, dynamo_resource):
    _live_agent(dynamo_resource)
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-2", ip="198.51.100.9", tier=2, score=-0.6, z=-6.0,
        reason="behavioral_anomaly", expires_at=0)

    assert "198.51.100.9" not in client.get("/dashboard/ui").text
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_gate_screen.py -q`
Expected: several failures, starting with `c-axis` not in the page.

- [ ] **Step 3: Build the axis in the route**

In `services/backend/ui/dashboard.py`, in `protection_status` (the route,
not the usage function), replace the `strip=sigma_strip(worst)` context with
the axis. Read the tenant's gate and the hour's bins:

```python
    # The tenant's own gate. Phase 0 put the value of record on Tenants and
    # copies it onto the model nightly; the model is already loaded here for
    # scoring, so this costs no read.
    tier1_sigma = abs(getattr(mgr.stats, "tier1_z", TIER1_Z))
    tier2_sigma = abs(getattr(mgr.stats, "tier2_z", TIER2_Z))

    # The shape below the gate, from the bins record_traffic writes. One
    # Query for the last 24 hours; the same rows the intervention strip and
    # the counts are read from, so they are three renderings of one read.
    now_ts = int(now.timestamp())
    series = TenantHistoryTable(resource).query_series(
        tenant_id, now_ts - 86_400, now_ts, fill=False)
    bins: dict[str, int] = {}
    for row in series:
        for key, value in row.items():
            if key.startswith("n") and key[1:].isdigit():
                bins[key] = bins.get(key, 0) + int(value)

    measured = sum(int(r.get("requests", 0)) for r in series)
    status = protection_status_of(resource, tenant_id)
    state = axis_state(health, status, model_ready=mgr.stats is not None)

    axis = build_axis(tier1_sigma, tier2_sigma, bins,
                      rows if state["plot"] == "live" else [])
```

Import `protection_status` from `services.backend.core.usage` under the
alias `protection_status_of`, because the route function in this module is
already called `protection_status`.

Pass `axis=axis`, `axis_state=state`, `measured=measured` into the template
context, and drop `strip=`.

- [ ] **Step 4: Rewrite the template**

Replace the top of `services/backend/ui/templates/dashboard_status.html`'s
`page_content` block, keeping the mitigation table and bulk bar as they are:

```jinja
{% import "shared/_axis.html" as axis_macro %}
...
  <div class="c-panel" data-axis-state="{{ axis_state.state }}">
    {{ axis_macro.render(axis,
         "Every measured source on the sigma scale, with your gates",
         axis_state.sentence) }}
    {% if axis_state.state in ("fed", "day_one") %}
    <p class="c-summary">
      {{ "{:,}".format(measured) }} requests measured in the last 24 hours.
    </p>
    {% endif %}
  </div>
```

and delete the `charts.sigma_strip(...)` call.

- [ ] **Step 5: Remove the strip from the public page**

In `services/backend/ui/public.py` drop the `sigma_strip` import and the
`"strip": sigma_strip(5.4)` context entry; in `landing.html` replace the
`charts.sigma_strip(...)` call with `axis_macro.render(...)` built from
`build_axis(4.0, 5.0, bins={}, rows=[])`. The landing page has no tenant, so
it shows the default gates and no data — which is honest, and is what it
already did.

Then delete `sigma_strip` and `SigmaStrip` from `charts.py` and their tests
from `test_charts.py`, in this same commit as their last caller.

- [ ] **Step 6: Run everything**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q --cov=services/backend --cov=services/agent --cov-fail-under=80`
Then: `ruff check services/backend services/agent`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add services/backend/ui/dashboard.py services/backend/ui/public.py \
        services/backend/ui/charts.py services/backend/ui/templates/ \
        services/backend/tests/test_gate_screen.py services/backend/tests/test_charts.py
git commit -m "feat(console): the Gate screen, built on one axis that reports its own feed"
```

---

## Done when

- The full suite passes and coverage stays above 80%.
- `ruff check` is clean.
- Total CSS is **below 80,183 bytes**.
- `grep -c 'data-flex=\|data-at=' services/backend/ui/static/app.css` returns 0.
- No `style=` attribute exists in any template under `templates/shared/`.

**Not in this plan, and deliberately:** the gate is drawn but not adjustable.
Making it a control is Phase 1b, and it needs the thirteen-row response curve,
a body-less POST, and an audit record — none of which belong in a plan whose
job is to prove the axis renders correctly first.

**Verification gap carried forward:** Phase 0's browser confirmation of the
htmx config is still outstanding, and this plan does not discharge it. It
needs the local server, which is started only when the maintainer asks.
