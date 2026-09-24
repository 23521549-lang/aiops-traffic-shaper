# Phase 1c — the two zooms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make zoom 1 (`?ip=`) answer job #2 the way the spec describes — the
appeal sitting beside the evidence line it rebuts, the selected source lit on
the axis, and a historic episode openable — and build zoom 2 (`?ip=&feature=`)
on measured data only.

**Architecture:** Both zooms are the same axis primitive read at a narrower
scope. Zoom 1 already exists as a detail pane but puts every verb in a corner,
which is the exact failure the rebuild was called for. Zoom 2 is new and is
constrained hard by what is actually stored: seven feature values frozen onto
each hourly episode, plus a per-tenant mean and standard deviation. That is a
position and a series, and it is not a distribution — so a curve must not be
drawn.

**Tech Stack:** FastAPI, Jinja2, htmx 2.0.4 (vendored), server-rendered SVG,
DynamoDB single-table.

**Spec:** `docs/ui-rebuild/05-spec-thiet-ke.md` §4.2, §4.3, §3.2, §3.3

## Global Constraints

- No em dash may appear in any rendered UI string.
- CSP is `default-src 'self'; script-src 'self'; style-src 'self'`. No inline
  `style=` attribute, no inline `<script>`. SVG presentation attributes
  (`x`, `width`, `points`, `d`, `fill`) are not CSS and are unaffected.
- Every mutation is a body-less POST with its values in the query string, sent
  by htmx so the CSRF token is inherited from the `hx-headers` on the console
  wrapper. Behind CloudFront's OAC a POST body needs `x-amz-content-sha256`,
  which only `signed-post.js` can compute, and that file has one caller and
  must keep one.
- Total CSS across `services/backend/ui/static/*.css` stays under 80,000 bytes.
- No new DynamoDB read on the scoring path. Console pages may add small
  GetItems; they may not add a Query or a Scan.
- Never invent a reading. Where statistics are missing, say so; where only a
  mean and a standard deviation exist, state them and draw a position, never
  a shape.
- Coverage gate: 80%. Run `ruff check` as a separate command from pytest.

---

## Task 1: The selected source is lit on the axis it sits on

Opening a source changes the list and leaves the axis identical, so the one
picture on the screen does not react to the selection at all. The axis is the
product's primary object; if it does not move when the operator picks
something, the detail pane reads as a separate page that happens to be beside
it.

**Files:**
- Modify: `services/backend/ui/charts.py` (`source_marks`, `build_axis`)
- Modify: `services/backend/ui/templates/shared/_axis.html`
- Modify: `services/backend/ui/static/app.css`
- Modify: `services/backend/ui/dashboard.py` (pass the selected ip)
- Test: `services/backend/tests/test_axis_selection.py` (create)

**Interfaces:**
- Consumes: `Mark(x, sigma, css, ip)` and `build_axis(tier1_sigma,
  tier2_sigma, bins, rows)` from Task 1 of Phase 1a.
- Produces: `Mark.selected: bool`; `source_marks(rows, selected_ip=None)`;
  `build_axis(tier1_sigma, tier2_sigma, bins, rows, selected_ip=None)`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_axis_selection.py`:

```python
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
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_axis_selection.py -q`
Expected: `TypeError: source_marks() got an unexpected keyword argument 'selected_ip'`

- [ ] **Step 3: Add the field and the parameter**

In `services/backend/ui/charts.py`, add `selected: bool = False` to `Mark`,
then:

```python
def source_marks(rows: list[dict], selected_ip: str | None = None) -> list[Mark]:
    marks = []
    for row in rows:
        z = row.get("z")
        if z is None:
            continue
        ip = row.get("ip", "")
        marks.append(Mark(x=axis_x(z), sigma=abs(z),
                          css=_tier_css(abs(z)).replace("c-bar", "c-mark"),
                          ip=ip,
                          selected=bool(selected_ip) and ip == selected_ip))
    return marks
```

and thread `selected_ip` through `build_axis` into that call.

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_axis_selection.py -q`
Expected: PASS

- [ ] **Step 5: Draw it**

In `shared/_axis.html`, the mark line becomes:

```jinja
    <line class="{{ m.css }}{% if m.selected %} c-mark--on{% endif %}"
          x1="{{ m.x }}" y1="0" x2="{{ m.x }}" y2="{{ a.height }}"/>
```

Append to `app.css`:

```css
/* Selection is a second state on top of severity, never instead of it. The
 * halo is drawn with stroke-width and a dash so it survives greyscale, the
 * same reason the tier ramp is never colour alone. */
.c-mark--on { stroke-width: 3; }
```

- [ ] **Step 6: Pass it in**

In `dashboard.py`'s `status_page`, the `build_axis` call takes
`selected_ip=selected["ip"] if selected else None`.

- [ ] **Step 7: Full suite, lint, commit**

```bash
PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q
ruff check services/backend services/agent
git add services/backend/ui/charts.py services/backend/ui/templates/shared/_axis.html \
        services/backend/ui/static/app.css services/backend/ui/dashboard.py \
        services/backend/tests/test_axis_selection.py
git commit -m "feat(charts): the axis reacts when the operator picks a source"
```

---

## Task 2: The appeal sits beside the evidence line it rebuts

Spec §4.2, and the single sharpest sentence in the design report: *every verb
is in a corner*. Today the decomposition table lists seven reasons and the
only action lives at the bottom of the pane, unattached to any of them. An
operator who has just read "POST ratio +0.4 sigma, not a driver" has to travel
to a corner to act on that reading, and the audit row records no connection
between the two.

Each driver row gets an Allow control **on that row**, and the appeal carries
the feature it was made from into the audit trail. The bottom-of-pane button
stays for the case where no single feature explains it.

**Files:**
- Modify: `services/backend/ui/templates/dashboard_status.html`
- Modify: `services/backend/ui/dashboard.py` (`whitelist_one` takes `because`)
- Modify: `services/backend/ui/static/app.css`
- Test: `services/backend/tests/test_appeal_beside_evidence.py` (create)

**Interfaces:**
- Consumes: `decompose(vector, means, stds) -> list[dict]` with keys `name`,
  `label`, `value`, `normal`, `sigma`, `display`, `drives`, from
  `services/backend/ui/presenters.py`.
- Consumes: the existing `POST /dashboard/ui/whitelist/{ip}?back=status` route.
- Produces: that route also accepting `because: str = ""`, recorded on the
  audit row as the feature name the appeal was made from.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_appeal_beside_evidence.py`:

```python
"""The verb next to the evidence, not in a corner.

Two competent redesigns of this product still read as dashboards, and the
reason was the same both times: the screen explained a decision in seven
lines and put the only response to it somewhere else. An operator who has
just read the line that convinced them has to travel to act on it, and
nothing afterwards records which line it was.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantHistoryTable, TenantsTable,
    create_all_tables,
)
from services.backend.main import app
from services.backend.ml.model import ModelManager
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    ModelManager._cache.clear()
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    now = datetime.now(timezone.utc).isoformat()
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-1",
                                     agent_label="web-01", registered_at=now,
                                     last_seen_at=now, agent_version="1.4.0",
                                     api_key_hash="h", status="active")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"custom:tenant_id": "t-1", "email": "ops@example.com"})})
    return c


def _blocked_source(resource):
    """One blocked IP with a full feature vector, and a model to measure it
    against, so the decomposition renders."""
    import numpy as np

    from services.backend.ml.training import train_and_save

    rng = np.random.default_rng(7)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(resource, "t-1", rows, stage="production")
    MitigationStateTable(resource).put(
        tenant_id="t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-6.2,
        reason="behavioral_anomaly", expires_at=2_000_000_000,
        features=[40.0, 0.9, 90_000.0, 9.0, 0.99, 6.0, 0.99])


def test_every_driver_row_carries_its_own_appeal(client, dynamo_resource):
    _blocked_source(dynamo_resource)

    page = client.get("/dashboard/ui?ip=10.0.0.7").text
    table = page[page.index("c-why"):page.index("</table>", page.index("c-why"))]

    assert "because=" in table


def test_the_appeal_names_the_feature_it_was_made_from(client, dynamo_resource):
    _blocked_source(dynamo_resource)

    page = client.get("/dashboard/ui?ip=10.0.0.7").text

    assert "because=post_ratio" in page


def test_the_recorded_appeal_says_which_line_convinced_them(client, dynamo_resource):
    _blocked_source(dynamo_resource)
    client.post("/dashboard/ui/whitelist/10.0.0.7?back=status&because=post_ratio",
                headers={"X-CSRF-Token": client.cookies["csrf_token"]})

    rows = TenantHistoryTable(dynamo_resource).query_settings(
        "t-1", 0, 2_000_000_000)
    appeals = [r for r in rows if r.get("what") == "whitelist"]

    assert appeals and appeals[0].get("because") == "post_ratio"


def test_an_appeal_with_no_feature_is_still_recorded(client, dynamo_resource):
    """The corner button stays: sometimes no single line explains it, and an
    operator forced to attribute their reasoning to one feature would pick
    one at random."""
    _blocked_source(dynamo_resource)
    client.post("/dashboard/ui/whitelist/10.0.0.7?back=status",
                headers={"X-CSRF-Token": client.cookies["csrf_token"]})

    rows = TenantHistoryTable(dynamo_resource).query_settings(
        "t-1", 0, 2_000_000_000)

    assert [r for r in rows if r.get("what") == "whitelist"]


def test_an_invented_feature_name_is_refused(client, dynamo_resource):
    """`because` is reflected into an audit row that a dispute may later turn
    on. It is checked against the seven real feature names rather than
    stored as given."""
    _blocked_source(dynamo_resource)
    response = client.post(
        "/dashboard/ui/whitelist/10.0.0.7?back=status&because=<script>",
        headers={"X-CSRF-Token": client.cookies["csrf_token"]})

    assert response.status_code == 400


def test_a_source_with_no_decomposition_offers_no_per_line_appeal(client, dynamo_resource):
    """Models trained before per-feature statistics existed produce no rows.
    A pane that shows the corner button and no lines is correct; one that
    shows empty lines with buttons on them is not."""
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="10.0.0.8", tier=1, score=-0.2, z=-4.4,
        reason="behavioral_anomaly", expires_at=2_000_000_000)

    page = client.get("/dashboard/ui?ip=10.0.0.8").text

    assert "because=" not in page
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_appeal_beside_evidence.py -q`
Expected: failures on the missing `because=` markup and the missing 400.

- [ ] **Step 3: Accept and validate `because` on the route**

In `dashboard.py`, `whitelist_one` gains `because: str = ""` and validates it
before anything else:

```python
    from services.backend.ml.feature_engineering import FEATURE_NAMES

    # Reflected into an audit row a dispute may later turn on, so it is
    # checked against the seven real names rather than stored as given.
    if because and because not in FEATURE_NAMES:
        raise HTTPException(status_code=400,
                            detail="That is not one of the measured features.")
```

and passes `because=because or None` into the `record_setting` audit write
already made there.

- [ ] **Step 4: Put the verb on the line**

In `dashboard_status.html`, each row of the `c-why` table gains a final cell,
rendered only for a row that drives the decision:

```jinja
          <td class="c-why-act">
            {% if f.drives %}
            <button type="button" class="c-btn c-btn--sm"
                    hx-post="/dashboard/ui/whitelist/{{ selected.ip }}?back=status&because={{ f.name }}"
                    hx-target="body" hx-swap="none"
                    hx-confirm="Allow {{ selected.ip }} because {{ f.label|lower }} is explainable? It stops being slowed or blocked immediately, and is left out of future model training."
                    aria-label="Allow {{ selected.ip }} because {{ f.label|lower }} is explainable">
              Explainable
            </button>
            {% endif %}
          </td>
```

with a matching `<th scope="col"><span class="sr-only">Appeal</span></th>` in
the head.

- [ ] **Step 5: Style it**

Append to `app.css`:

```css
/* The verb on the evidence line. Right-aligned and quiet: it is available on
 * every driver row, so seven loud buttons would read as the point of the
 * table rather than a response to it. */
.c-why-act { text-align: right; white-space: nowrap; }
.c-why-act .c-btn { font-size: var(--text-xs); }
```

- [ ] **Step 6: Run everything, lint, commit**

```bash
PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q
ruff check services/backend services/agent
git add services/backend/ui/templates/dashboard_status.html \
        services/backend/ui/dashboard.py services/backend/ui/static/app.css \
        services/backend/tests/test_appeal_beside_evidence.py
git commit -m "feat(console): the appeal sits on the line of evidence it rebuts"
```

---

## Task 3: Zoom 2 — one feature, on measured data only

Spec §4.3. `?ip=&feature=` shows one of the seven dimensions: where this
source sits on it, what this tenant's normal is on it, and how that source has
moved along it over the retained window.

The constraint that shapes the whole screen: the only per-tenant statistics
stored are a **mean and a standard deviation** per feature. That is enough for
a position and not remotely enough for a shape, so no curve, histogram or
violin may be drawn. The per-hour series is real measured data, taken from the
`last_features` vector already frozen onto each hourly episode by Phase 0.

**Files:**
- Create: `services/backend/ui/templates/shared/_feature.html`
- Modify: `services/backend/ui/charts.py` (`feature_track`)
- Modify: `services/backend/ui/dashboard.py` (`status_page` reads `feature`)
- Modify: `services/backend/ui/templates/dashboard_status.html`
- Modify: `services/backend/ui/static/app.css`
- Test: `services/backend/tests/test_feature_zoom.py` (create)

**Interfaces:**
- Consumes: `MitigationEpisode.last_features: list[float]` and
  `stats_version: str | None` from `services/backend/schemas/history.py`.
- Consumes: `FEATURE_NAMES` from
  `services/backend/ml/feature_engineering.py`, and `ScoreStats.feature_means`
  / `.feature_stds` / `.version`.
- Produces: `charts.feature_track(episodes, index, mean, std) ->
  list[FeaturePoint]` where `FeaturePoint(hour_start: int, sigma: float |
  None, x: float, y: float)`, and `charts.FEATURE_TRACK_HEIGHT`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_feature_zoom.py`:

```python
"""One feature, and only what was actually measured about it.

The per-tenant statistics are a mean and a standard deviation per feature.
That is a position and it is not a distribution: a bell curve drawn from
those two numbers would be an assertion about the shape of this tenant's
traffic that nothing in the system has ever measured. What IS measured is the
value frozen onto each hourly episode, which is a real series.
"""
import pytest

from services.backend.schemas.history import MitigationEpisode
from services.backend.ui.charts import feature_track


def _episodes():
    return [
        MitigationEpisode(ip="10.0.0.7", hour_start=3600, first_ts=3600,
                          last_ts=3600, tier1_count=1,
                          last_features=[1.0, 0.0, 700.0, 0.1, 0.5, 1.0, 0.05]),
        MitigationEpisode(ip="10.0.0.7", hour_start=7200, first_ts=7200,
                          last_ts=7200, tier1_count=1,
                          last_features=[3.0, 0.0, 700.0, 0.1, 0.5, 1.0, 0.05]),
    ]


def test_a_point_per_episode_in_the_chosen_dimension():
    track = feature_track(_episodes(), index=0, mean=1.0, std=1.0)

    assert [p.sigma for p in track] == [0.0, 2.0]


def test_an_episode_with_no_vector_is_a_hole_not_a_zero():
    """Episodes written before Phase 0 carry no features. Plotting them at
    the mean would draw a source sitting exactly on this tenant's normal in
    an hour when it was being blocked."""
    episodes = _episodes() + [MitigationEpisode(
        ip="10.0.0.7", hour_start=10800, first_ts=10800, last_ts=10800,
        tier1_count=1)]

    assert feature_track(episodes, index=0, mean=1.0, std=1.0)[-1].sigma is None


def test_a_degenerate_baseline_measures_nothing_rather_than_dividing():
    """std is zero when every training bucket had the same value on this
    feature. There is no sigma to report, and reporting zero would say the
    source is normal."""
    track = feature_track(_episodes(), index=0, mean=1.0, std=0.0)

    assert all(p.sigma is None for p in track)


def test_a_short_vector_is_refused_rather_than_mismatched():
    """A vector shorter than the feature list would line the wrong number up
    with the wrong baseline, and the result would look entirely plausible."""
    episodes = [MitigationEpisode(ip="10.0.0.7", hour_start=3600,
                                  first_ts=3600, last_ts=3600, tier1_count=1,
                                  last_features=[1.0, 2.0])]

    assert feature_track(episodes, index=5, mean=1.0, std=1.0)[0].sigma is None


def test_the_track_is_ordered_by_hour():
    track = feature_track(list(reversed(_episodes())), index=0, mean=1.0, std=1.0)

    assert [p.hour_start for p in track] == [3600, 7200]


def test_x_is_time_and_y_is_sigma():
    track = feature_track(_episodes(), index=0, mean=1.0, std=1.0)

    assert track[0].x == 0
    assert track[-1].x > track[0].x
    assert track[1].y < track[0].y   # further out is higher on screen


def test_one_episode_is_a_point_not_a_division_by_zero():
    track = feature_track(_episodes()[:1], index=0, mean=1.0, std=1.0)

    assert len(track) == 1 and track[0].x == 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_feature_zoom.py -q`
Expected: `ImportError: cannot import name 'feature_track'`

- [ ] **Step 3: Write it**

Append to `services/backend/ui/charts.py`:

```python
FEATURE_TRACK_WIDTH = 600.0
FEATURE_TRACK_HEIGHT = 120.0
# The same ceiling the sigma axis uses, so a point at 6 sigma here and a mark
# at 6 sigma there are at the same height off the same scale.
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
    tenant's traffic that nothing has measured. The value frozen onto each
    hourly episode is a real measurement, and a series of them is a real
    series.

    `sigma` is None wherever it genuinely cannot be computed - no vector, a
    vector too short to line up, or a baseline with no spread. Those are holes
    and must be drawn as holes: a hole says the instrument had nothing, a zero
    says the source was normal, and in an hour when it was being blocked
    those are opposite claims.
    """
    ordered = sorted(episodes, key=lambda e: e.hour_start)
    span = max(len(ordered) - 1, 1)
    points = []
    for i, e in enumerate(ordered):
        vector = list(e.last_features or [])
        sigma = None
        if std > 0 and index < len(vector):
            sigma = (float(vector[index]) - mean) / std
        magnitude = min(abs(sigma), FEATURE_TRACK_CEILING) if sigma is not None else 0.0
        points.append(FeaturePoint(
            hour_start=e.hour_start,
            sigma=sigma,
            x=round(i * FEATURE_TRACK_WIDTH / span, 2),
            y=round(FEATURE_TRACK_HEIGHT * (1 - magnitude / FEATURE_TRACK_CEILING), 2),
        ))
    return points
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_feature_zoom.py -q`
Expected: PASS

- [ ] **Step 5: Write the macro**

Create `services/backend/ui/templates/shared/_feature.html`:

```jinja
{% macro render(f, track, height, width, back_url) %}
{# Zoom 2. One dimension of one source.

   There is deliberately no distribution curve here. The stored statistics
   are a mean and a standard deviation, which locate a point and say nothing
   about shape, and drawing a bell from them would be an assertion about this
   tenant's traffic that nothing in the system has measured.

   A hole in the track is a hole. Bridging it would draw a straight line
   through hours the instrument has no reading for. #}
<div class="c-feature">
  <div class="c-feature-head">
    <div class="c-section-label">{{ f.label }}</div>
    <a class="c-btn c-btn--sm" href="{{ back_url }}">Back to all seven</a>
  </div>

  <dl class="c-kv">
    <dt>This source</dt><dd>{{ f.value }}</dd>
    <dt>Your normal</dt><dd>{{ f.normal }}</dd>
    <dt>Distance</dt><dd>{{ f.display }}</dd>
  </dl>

  {% if track %}
  <svg class="c-feature-track" viewBox="0 0 {{ width }} {{ height }}"
       preserveAspectRatio="none" focusable="false" aria-hidden="true">
    {% for run in track %}
    {% if run|length > 1 %}
    <polyline class="c-feature-line"
              points="{% for p in run %}{{ p.x }},{{ p.y }} {% endfor %}"/>
    {% endif %}
    {% for p in run %}
    <circle class="c-feature-dot" cx="{{ p.x }}" cy="{{ p.y }}" r="2.5"/>
    {% endfor %}
    {% endfor %}
  </svg>
  <p class="c-axis-caption">
    This source on {{ f.label|lower }}, hour by hour. Higher is further from
    your normal. A gap is an hour with no reading, not an hour at normal.
  </p>
  {% else %}
  <p class="c-summary">
    No hourly record of this source on this feature yet. Episodes started
    carrying the measured vector recently, so a source first seen before then
    has a decision but no track.
  </p>
  {% endif %}

  <p class="c-summary">
    Read only. A per-feature threshold is not built: the gate is measured on
    the combination of all seven, and splitting it would need a second model.
  </p>
</div>
{% endmacro %}
```

The `track` passed in is a list of **runs** — consecutive points that have a
sigma — so a hole genuinely breaks the line rather than being bridged.

- [ ] **Step 6: Wire it into the route**

In `dashboard.py`'s `status_page`, after the decomposition is built:

```python
    # Zoom 2. Only reachable from a selected source, and only for one of the
    # seven real names: `feature` is used to index a vector, so a value that
    # is not on the list must never reach it.
    zoom = None
    if selected and feature and selected.get("features_breakdown"):
        from services.backend.ml.feature_engineering import FEATURE_NAMES

        if feature in FEATURE_NAMES:
            index = FEATURE_NAMES.index(feature)
            row = next((r for r in selected["features_breakdown"]
                        if r["name"] == feature), None)
            episodes = list_history(since=now_ts - 7 * 86_400, until=now_ts,
                                    tenant_id=tenant_id, resource=resource)
            mine = [e for e in episodes if e.ip == selected["ip"]]
            points = feature_track(
                mine, index,
                (getattr(stats, "feature_means", []) or [0.0] * 7)[index],
                (getattr(stats, "feature_stds", []) or [0.0] * 7)[index])
            zoom = {"f": row, "runs": _runs(points)}
```

with a small helper beside it:

```python
def _runs(points: list) -> list[list]:
    """Consecutive readings, split on every hole.

    A single polyline through the lot would draw a straight line across the
    hours that have no reading, which is precisely the claim the hole exists
    to avoid making.
    """
    runs, current = [], []
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
```

`status_page` gains `feature: str | None = None` in its signature and passes
`zoom=zoom` into the template. `dashboard_status.html` renders
`shared/_feature.html` in place of the seven-row table when `zoom` is set,
with `back_url` of `/dashboard/ui?ip={{ selected.ip }}`, and each feature
label in the seven-row table becomes a link to
`/dashboard/ui?ip={{ selected.ip }}&feature={{ f.name }}`.

- [ ] **Step 7: Style it**

Append to `app.css`:

```css
/* --- zoom 2, one feature ----------------------------------------------- */

.c-feature-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: var(--space-3);
}
.c-feature-track {
  width: 100%; height: 120px; margin-top: var(--space-3);
  background: var(--surface-sunken); border-radius: var(--radius-sm);
}
.c-feature-line { fill: none; stroke: var(--accent); stroke-width: 2; }
.c-feature-dot { fill: var(--accent); }
```

- [ ] **Step 8: Run everything, lint, check the budget, commit**

```bash
PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q \
  --cov=services/backend --cov=services/agent --cov-fail-under=80
ruff check services/backend services/agent
wc -c services/backend/ui/static/*.css
git add services/backend/ui/templates/ services/backend/ui/charts.py \
        services/backend/ui/dashboard.py services/backend/ui/static/app.css \
        services/backend/tests/test_feature_zoom.py
git commit -m "feat(console): one feature, on the only data that measures it"
```

---

## Task 4: A historic episode opens the same way a live one does

Spec §4.2 closes with a condition that Phase 0 has now met: the per-feature
breakdown of a historic episode is openable once `last_features` is stored. It
is, so the affordance may be drawn.

One thing must be said on that screen and nowhere else: the vector is frozen
at decision time and the baseline is read live, so a retrain between the two
makes the two halves of the explanation describe different models. The episode
carries `stats_version` for exactly this, and the screen must use it.

**Files:**
- Modify: `services/backend/ui/dashboard.py` (`history_page`)
- Modify: `services/backend/ui/templates/dashboard_history.html`
- Test: `services/backend/tests/test_historic_decomposition.py` (create)

**Interfaces:**
- Consumes: `decompose`, `MitigationEpisode.last_features`,
  `MitigationEpisode.stats_version`, `ScoreStats.version`.
- Produces: `history_page(request, days, ip, resource)` accepting an `ip`
  selection, and a `drifted: bool` flag in the template context.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_historic_decomposition.py`:

```python
"""Why a decision was taken, after the fact.

The whole argument for this product is that it can answer that question in
the customer's own units. Answering it only while the block is still active
makes it a monitoring feature; answering it a week later, in a ticket, is the
thing being sold.

The catch this screen exists to handle: `z` and the feature vector are frozen
at decision time and the baseline is read live. One nightly retrain between
them and the two halves of the explanation describe different models. Nothing
else in the product is in a position to notice.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, TenantHistoryTable, TenantsTable, create_all_tables,
)
from services.backend.main import app
from services.backend.ml.model import ModelManager
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    ModelManager._cache.clear()
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    now = datetime.now(timezone.utc).isoformat()
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-1",
                                     agent_label="web-01", registered_at=now,
                                     last_seen_at=now, agent_version="1.4.0",
                                     api_key_hash="h", status="active")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    return c


def _trained(resource):
    import numpy as np

    from services.backend.ml.training import train_and_save

    rng = np.random.default_rng(11)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(resource, "t-1", rows, stage="production")


def _episode(resource, *, version=None):
    now = int(datetime.now(timezone.utc).timestamp())
    TenantHistoryTable(resource).record_decision(
        "t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-6.2,
        hour_start=TenantHistoryTable.hour_of(now), now=now,
        features=[40.0, 0.9, 90_000.0, 9.0, 0.99, 6.0, 0.99],
        stats_version=version)


def test_an_episode_from_last_week_still_explains_itself(client, dynamo_resource):
    _trained(dynamo_resource)
    _episode(dynamo_resource)

    page = client.get("/dashboard/ui/history?ip=10.0.0.7").text

    assert "POST ratio" in page


def test_filtering_by_source_actually_filters(client, dynamo_resource):
    """The link from the detail pane has always pointed here. The route had
    no `ip` parameter, so it silently showed the unfiltered list and the
    operator read the wrong object."""
    _trained(dynamo_resource)
    _episode(dynamo_resource)
    now = int(datetime.now(timezone.utc).timestamp())
    TenantHistoryTable(dynamo_resource).record_decision(
        "t-1", ip="10.0.0.9", tier=1, score=-0.2, z=-4.4,
        hour_start=TenantHistoryTable.hour_of(now), now=now)

    page = client.get("/dashboard/ui/history?ip=10.0.0.7").text

    assert "10.0.0.9" not in page


def test_a_baseline_that_has_moved_since_is_said_plainly(client, dynamo_resource):
    _trained(dynamo_resource)
    _episode(dynamo_resource, version="an-older-model")

    page = client.get("/dashboard/ui/history?ip=10.0.0.7").text

    assert "retrained" in page.lower()


def test_a_baseline_that_has_not_moved_makes_no_such_claim(client, dynamo_resource):
    from services.backend.ml.registry import load_model_and_stats

    _trained(dynamo_resource)
    _, stats = load_model_and_stats(dynamo_resource, "t-1")
    _episode(dynamo_resource, version=stats.version)

    page = client.get("/dashboard/ui/history?ip=10.0.0.7").text

    assert "retrained" not in page.lower()


def test_an_episode_with_no_vector_says_so_instead_of_inventing_one(client, dynamo_resource):
    _trained(dynamo_resource)
    now = int(datetime.now(timezone.utc).timestamp())
    TenantHistoryTable(dynamo_resource).record_decision(
        "t-1", ip="10.0.0.7", tier=1, score=-0.2, z=-4.4,
        hour_start=TenantHistoryTable.hour_of(now), now=now)

    page = client.get("/dashboard/ui/history?ip=10.0.0.7").text

    assert "not recorded" in page.lower() or "does not carry" in page.lower()


def test_an_unknown_source_is_an_empty_filter_not_an_error(client, dynamo_resource):
    _trained(dynamo_resource)

    assert client.get("/dashboard/ui/history?ip=10.0.0.99").status_code == 200
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_historic_decomposition.py -q`
Expected: failures — `history_page` takes no `ip`, so nothing filters and
nothing decomposes.

- [ ] **Step 3: Give the route the parameter it has always been linked with**

`history_page` gains `ip: str | None = None`. The episode list filters on it
after the query (the query is by tenant and time window; filtering
server-side in Python costs no extra read and the window is already bounded).
The chart keeps using the **unfiltered** series, because a per-source chart of
total traffic would be a different quantity wearing the same axis.

- [ ] **Step 4: Decompose the selected episode**

When `ip` is set, take the most recent episode for it, load the model stats
once (the same `ModelManager` load the status page does), and build:

```python
    selected_episode = next((e for e in episodes if e.ip == ip), None)
    breakdown, drifted = [], False
    if selected_episode is not None:
        mgr = ModelManager()
        mgr.load(resource, tenant_id)
        stats = mgr.stats
        breakdown = decompose(selected_episode.last_features,
                              getattr(stats, "feature_means", None),
                              getattr(stats, "feature_stds", None))
        # Frozen vector, live baseline. A retrain between them makes the two
        # halves of the explanation describe different models, and this is
        # the only place in the product that can notice.
        drifted = bool(selected_episode.stats_version
                       and getattr(stats, "version", None)
                       and selected_episode.stats_version != stats.version)
```

- [ ] **Step 5: Draw it**

In `dashboard_history.html`, when `selected_episode` is set, render the same
`c-why` table the status pane uses, preceded when `drifted` by:

```jinja
    <div class="alert alert-warning">
      <strong>Your model has been retrained since this decision.</strong>
      The measurements below are the ones taken at the time. The normal they
      are compared against is today's, so the distances shown are not the
      distances this decision was made on.
    </div>
```

and, when the episode carries no vector:

```jinja
    <p class="c-summary">
      This episode does not carry the measurements that were taken. Episodes
      started recording them recently, so anything older explains itself by
      deviation alone.
    </p>
```

- [ ] **Step 6: Run everything, lint, commit**

```bash
PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q \
  --cov=services/backend --cov=services/agent --cov-fail-under=80
ruff check services/backend services/agent
git add services/backend/ui/dashboard.py \
        services/backend/ui/templates/dashboard_history.html \
        services/backend/tests/test_historic_decomposition.py
git commit -m "feat(console): an episode from last week explains itself, and says when the baseline moved"
```

---

## Self-review

**Spec coverage.** §4.2 has four claims: the axis holds and the source lights
up (Task 1), the seven-feature table (already shipped in Phase 1a), the appeal
beside the evidence (Task 2), the permalink (already true). Its closing
condition, historic episodes once `last_features` ships, is Task 4. §4.3 is
Task 3, read-only as the spec states. The §4.4 filtering defect is fixed in
Task 4 because Task 4's own tests cannot pass while it stands; the rest of
§4.4 is Phase 1d.

**Type consistency.** `Mark.selected` (Task 1) is read only by `_axis.html`.
`FeaturePoint` (Task 3) is produced by `feature_track` and consumed by `_runs`
and `_feature.html`. `decompose` rows carry `name`, which Tasks 2 and 3 both
use as the `because=` and `feature=` value and both validate against
`FEATURE_NAMES` before use.

**Known risk, stated rather than designed around.** Task 3 adds a 7-day
`list_history` Query to the status page, but **only** when `feature` is set,
which happens on no default page load. Task 4 adds a `ModelManager` load to
the history page, which is cached per warm container and costs nothing on the
common path. Neither touches the scoring path.
