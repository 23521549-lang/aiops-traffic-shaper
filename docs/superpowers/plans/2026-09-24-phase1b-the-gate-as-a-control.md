# Phase 1b — The gate becomes a control

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a customer move their own enforcement threshold, from the axis that reports it, with the consequence of the move visible before they commit to it.

**Architecture:** The gate has exactly thirteen legal positions, so the entire response curve is server-rendered at once and the control is thirteen rows rather than a slider. Each row is a body-less POST, the same shape the theme toggle already uses, so it costs no JavaScript and needs no CloudFront payload hash. Phase 0 already stores the per-tenant value on `Tenants`, the thirteen bins that make the curve, and the audit row that records the change; this plan is the screen and the route that connect them.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, server-rendered HTML forms. Zero JavaScript added.

**Spec:** `docs/ui-rebuild/05-spec-thiet-ke.md` — sections 1.1, 2.4, 2.5, 5. Read section 5 first: the argument for thirteen rows over a slider is the whole design.

**Depends on:** Phase 0 (`4f20729`) for `Tenants.tier1_z`, the `n300`…`n600` bins and `TenantHistoryTable.record_setting`. Phase 1a (`8e0927b`) for the axis those gates are drawn on.

## Global Constraints

- **Test runner.** pytest and ruff as **separate** commands; read the `N failed, M passed` line, never the exit code.
- **TDD**, tests named after the defect, written failing first.
- **Commits:** one `-m`, explicit paths, no `git add -A` at the repo root, no AI attribution.
- **Every mutation is a body-less POST.** Values ride in the query string. A form-encoded body needs `x-amz-content-sha256`, which only `signed-post.js` can compute, and that file has exactly one caller and must keep exactly one.
- **CSP.** No inline `style=`, no new JavaScript. The control must work with scripting disabled.
- **CSS budget:** three sheets, currently 71,314 bytes. This plan may add, and must stay under 80,000.
- **No em dash in anything rendered.**
- **Never uppercase a string that can contain a sigma.**

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `services/backend/ui/charts.py` | the thirteen-row response curve, as numbers | 1 |
| `services/backend/core/tables.py` | `TenantsTable.set_threshold` | 2 |
| `services/backend/ui/dashboard.py` | the `POST /dashboard/ui/gate` route | 2 |
| `services/backend/ui/templates/shared/_gate.html` | the thirteen rows | 3 |
| `services/backend/ui/templates/dashboard_status.html` | the control, beside the axis it moves | 3 |
| `services/backend/ui/static/app.css` | the curve's rules | 3 |

---

## Task 1: The response curve

Thirteen rows of "at this gate, this many sources". The thing that makes a rendered curve better than a drag: it shows all thirteen answers at once instead of one at a time.

**Files:**
- Modify: `services/backend/ui/charts.py`
- Test: `services/backend/tests/test_gate_curve.py` (create)

**Interfaces:**
- Produces: `GateOption(sigma, count, width, current, recommended)` and
  `gate_curve(bins: dict[str, int], current_sigma: float, blocked_total: int = 0) -> list[GateOption]`

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_gate_curve.py`:

```python
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
    sources than raising it."""
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


def test_the_lowest_positions_are_flagged_as_not_recommended():
    """ADR-006 measured 0.82% false positives at 3.5 sigma and the rate
    climbs steeply below it. The control may offer these and must not
    present them as ordinary."""
    curve = {o.sigma: o for o in gate_curve({}, 4.0)}

    assert curve[3.0].recommended is False
    assert curve[3.5].recommended is False
    assert curve[4.0].recommended is True
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_gate_curve.py -q`
Expected: FAIL, `ImportError: cannot import name 'gate_curve'`

- [ ] **Step 3: Implement it**

Append to `services/backend/ui/charts.py`:

```python
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
    gate, which is what `blocked_total` adds. Leaving those out would tell
    an operator that lowering the gate catches FEWER sources than raising
    it, which is backwards.

    A rendered curve rather than a slider because there are thirteen
    answers and a slider shows one of them at a time.
    """
    from services.backend.core.tables import TenantHistoryTable

    edges = TenantHistoryTable.NEAR_BINS
    counts = [int(bins.get("n%d" % round(s * 100), 0)) for s in edges]

    cumulative = []
    running = int(blocked_total)
    for count in reversed(counts):
        cumulative.append(running)
        running += count
    cumulative.reverse()
    # `cumulative[i]` is now everything strictly above edge i, so fold in
    # the bin at the line itself.
    totals = [c + n for c, n in zip(cumulative, counts)]
    totals = [t - n for t, n in zip(totals, counts)] if False else totals

    widest = max(totals) if totals else 0
    return [GateOption(sigma=s, count=t,
                       width=(t / widest) if widest else 0.0,
                       current=(abs(s - current_sigma) < 1e-9),
                       recommended=(s >= GATE_RECOMMENDED_FLOOR))
            for s, t in zip(edges, totals)]
```

- [ ] **Step 4: Run it and watch it pass**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_gate_curve.py -q`
Expected: 8 passed. If the cumulative arithmetic is off by one bin, fix the
loop rather than the test: the assertion `curve[3.0].count == 10` for bins
`{3.0: 5, 3.25: 3, 3.5: 2}` is the definition.

- [ ] **Step 5: Commit**

```bash
git add services/backend/ui/charts.py services/backend/tests/test_gate_curve.py
git commit -m "feat(charts): the thirteen answers a gate control has to show at once"
```

---

## Task 2: Moving the gate

**Files:**
- Modify: `services/backend/core/tables.py`
- Modify: `services/backend/ui/dashboard.py`
- Test: `services/backend/tests/test_gate_move.py` (create)

**Interfaces:**
- Produces: `TenantsTable.set_threshold(tenant_id, which, sigma) -> float | None` returning the previous value, and `POST /dashboard/ui/gate?tier=1&sigma=4.25`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_gate_move.py`:

```python
"""A customer moving their own enforcement threshold.

This is the change the whole rebuilt console exists for. TIER1_Z was a
module constant, so the product could say "6.4 standard deviations outside
your normal" and offer no way to say "for me, act at 4.5". Every verb was in
a corner, which is why two competent redesigns still read as dashboards.

Moving it is one of the four actions that earn an audit row: it changes
enforcement for all future traffic on a live site, and a reasonable person
could later dispute it with money attached.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    TenantHistoryTable, TenantsTable, create_all_tables,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


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
        cognito_test_keys["private_pem"],
        {"custom:tenant_id": "t-1", "email": "ops@example.com"})})
    return c


def _csrf(client):
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def _move(client, tier=1, sigma=4.25):
    return client.post(f"/dashboard/ui/gate?tier={tier}&sigma={sigma}",
                       headers=_csrf(client))


def test_moving_the_gate_stores_it_on_the_tenant(client, dynamo_resource):
    """On Tenants, not on Models: save_model rewrites the model item every
    night and would silently revert the operator."""
    _move(client, tier=1, sigma=4.25)

    tenant = TenantsTable(dynamo_resource).get(tenant_id="t-1")
    assert float(tenant["tier1_z"]) == -4.25


def test_the_stored_value_is_negative_because_z_is(client, dynamo_resource):
    """The screen speaks in magnitudes and the model speaks in z. Storing
    +4.25 would make classify() tier nothing at all, silently."""
    _move(client, tier=2, sigma=5.5)

    assert float(TenantsTable(dynamo_resource).get(tenant_id="t-1")["tier2_z"]) == -5.5


def test_the_move_is_recorded_with_who_and_both_values(client, dynamo_resource):
    history = TenantHistoryTable(dynamo_resource)
    _move(client, tier=1, sigma=4.25)

    rows = history.query_settings("t-1", 0, 2_000_000_000)

    assert len(rows) == 1
    assert rows[0]["actor"] == "ops@example.com"
    assert rows[0]["what"] == "tier1_z"
    assert float(rows[0]["new"]) == -4.25


def test_a_position_that_is_not_one_of_the_thirteen_is_refused(client, dynamo_resource):
    """The curve offers thirteen. Accepting 4.1 would set a gate whose
    consequence the screen cannot show, because no bin measures it."""
    response = _move(client, tier=1, sigma=4.1)

    assert response.status_code == 400
    assert "tier1_z" not in (TenantsTable(dynamo_resource).get(tenant_id="t-1") or {})


def test_the_slow_gate_may_not_be_placed_above_the_block_gate(client, dynamo_resource):
    """Then every source past 5 would be blocked without ever being slowed,
    and the middle band would be empty and meaningless."""
    _move(client, tier=2, sigma=4.5)
    response = _move(client, tier=1, sigma=5.0)

    assert response.status_code == 400
    assert float(TenantsTable(dynamo_resource).get(tenant_id="t-1")["tier1_z"]) != -5.0


def test_one_tenant_cannot_move_another_tenants_gate(client, dynamo_resource):
    TenantsTable(dynamo_resource).put(tenant_id="t-2", name="Globex",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    _move(client, tier=1, sigma=4.25)

    assert "tier1_z" not in (TenantsTable(dynamo_resource).get(tenant_id="t-2") or {})


def test_the_operator_lands_back_on_the_screen_they_moved_it_from(client):
    """The consequence of the move is the thing they were reading. A
    response they never see is the defect hx-swap="none" already caused
    once."""
    assert _move(client).headers.get("HX-Redirect") == "/dashboard/ui"


def test_the_request_carries_no_body(client):
    """Behind CloudFront's OAC a body needs x-amz-content-sha256, which only
    signed-post.js can compute, and that file has one caller and must keep
    one. The values ride in the query string, like the theme toggle."""
    import inspect

    from services.backend.ui import dashboard

    source = inspect.getsource(dashboard.move_gate)
    assert "Form(" not in source
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_gate_move.py -q`
Expected: FAIL, 404 on the route.

- [ ] **Step 3: Store the value**

In `services/backend/core/tables.py`, inside `TenantsTable`:

```python
    def set_threshold(self, tenant_id: str, which: str, z: float) -> float | None:
        """Move one of this tenant's gates, returning the previous value.

        The previous value is returned rather than looked up again by the
        caller, because the audit row needs both and a second GetItem to
        learn what you just overwrote is a read you already paid for.

        On Tenants rather than Models: the nightly retrain rewrites the
        model item, so a threshold living only there would be silently
        reverted every night by the very function that trains on it.
        """
        resp = self._table.update_item(
            Key={"tenant_id": tenant_id},
            UpdateExpression="SET #k = :z",
            ExpressionAttributeNames={"#k": which},
            ExpressionAttributeValues={":z": _to_dynamo_safe(float(z))},
            ConditionExpression="attribute_exists(tenant_id)",
            ReturnValues="UPDATED_OLD",
        )
        old = resp.get("Attributes", {}).get(which)
        return float(old) if old is not None else None
```

- [ ] **Step 4: Add the route**

In `services/backend/ui/dashboard.py`:

```python
@router.post("/dashboard/ui/gate", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def move_gate(tier: int = 1, sigma: float = 0.0,
              tenant_id: str = Depends(dashboard_auth),
              claims: dict = Depends(dashboard_claims),
              resource=Depends(get_dynamo_resource)):
    """Move one gate to one of its thirteen legal positions.

    Body-less: the values ride in the query string, the same shape the theme
    toggle uses, because a form-encoded body would need CloudFront's payload
    hash and the whole point of this control is that it needs no script.

    The position is checked against the thirteen the curve offers. Accepting
    4.1 would set a gate whose consequence the screen cannot show, because
    no bin measures it, and a control that can be put somewhere it cannot
    report on is a control that lies.
    """
    if sigma not in TenantHistoryTable.NEAR_BINS or tier not in (1, 2):
        raise HTTPException(status_code=400,
                            detail="That is not one of the available positions.")

    tenant = TenantsTable(resource).get(tenant_id=tenant_id) or {}
    other = float(tenant.get("tier2_z" if tier == 1 else "tier1_z",
                             TIER2_Z if tier == 1 else TIER1_Z))
    # The slow gate sits below the block gate. The other way round, every
    # source past the block line is blocked without ever being slowed, and
    # the middle band is empty and meaningless.
    if (tier == 1 and -sigma <= other) or (tier == 2 and -sigma >= other):
        raise HTTPException(
            status_code=400,
            detail="The slow gate has to sit closer to your normal than the "
                   "block gate.")

    which = "tier1_z" if tier == 1 else "tier2_z"
    old = TenantsTable(resource).set_threshold(tenant_id, which, -sigma)
    _try_history(TenantHistoryTable(resource).record_setting,
                 tenant_id, actor=actor_of(claims), what=which,
                 old=old, new=-sigma, now=int(time.time()))

    return Response(status_code=200, headers={"HX-Redirect": "/dashboard/ui"})
```

`_try_history` is in `api/routes/agent.py`; import it, or inline the same
try/except. The audit row is reporting: a throttled write must not lose the
threshold change the customer just made.

- [ ] **Step 5: Run it and watch it pass**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_gate_move.py -q`
Expected: 8 passed

- [ ] **Step 6: Run everything and lint**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q`
Then: `ruff check services/backend services/agent`

- [ ] **Step 7: Commit**

```bash
git add services/backend/core/tables.py services/backend/ui/dashboard.py \
        services/backend/tests/test_gate_move.py
git commit -m "feat(console): a customer can move the gate that judges their traffic"
```

---

## Task 3: The control, beside the axis it moves

**Files:**
- Create: `services/backend/ui/templates/shared/_gate.html`
- Modify: `services/backend/ui/templates/dashboard_status.html`
- Modify: `services/backend/ui/dashboard.py` (pass the curve)
- Modify: `services/backend/ui/static/app.css`
- Test: `services/backend/tests/test_gate_control.py` (create)

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_gate_control.py`:

```python
"""Explanation and control, in the same units, on the same screen.

This is the one thing the spec says must be true of the final design. A rule
engine explains itself with a rule id, and an id is not on a scale, so it
cannot be moved. Traffic Shaper's explanation is already a coordinate in the
customer's own measurement space, which is why it can double as a control
surface - and if the decomposition and the tuning end up in two different
places, the product has thrown away the only thing it owns.
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
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
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


def test_all_thirteen_positions_are_offered_at_once(client):
    """Not a slider. Thirteen rows, so every outcome is readable without a
    gesture."""
    page = client.get("/dashboard/ui").text

    assert page.count('name="sigma"') == 0      # no body, no named field
    assert page.count("/dashboard/ui/gate?") >= 13


def test_each_position_is_a_body_less_post(client):
    page = client.get("/dashboard/ui").text

    assert 'action="/dashboard/ui/gate?tier=1&amp;sigma=4.0"' in page \
        or 'action="/dashboard/ui/gate?tier=1&sigma=4.0"' in page


def test_the_control_needs_no_javascript(client):
    """It is a form and a submit button. With scripting off it still
    works, which is the reason it is not a drag."""
    page = client.get("/dashboard/ui").text
    control = page[page.index("gate?tier="):]

    assert "hx-" not in control[:400]


def test_the_current_position_is_marked_without_relying_on_colour(client):
    page = client.get("/dashboard/ui").text

    assert 'aria-current="true"' in page


def test_the_counts_come_from_this_tenants_own_measurements(client, dynamo_resource):
    hour = TenantHistoryTable.hour_of(int(datetime.now(timezone.utc).timestamp()))
    TenantHistoryTable(dynamo_resource).record_traffic(
        "t-1", hour, requests=10, bins={"n300": 41})

    assert "41" in client.get("/dashboard/ui").text


def test_the_not_recommended_positions_say_so_in_words(client):
    """ADR-006 measured the false-positive rate climbing steeply below 4
    sigma. Colour alone would not survive a screenshot in a ticket."""
    page = client.get("/dashboard/ui").text

    assert "more false" in page.lower() or "not recommended" in page.lower()


def test_the_control_never_offers_a_link_to_see_the_sub_threshold_sources(client):
    """No IP below the gate is stored - only the shape. A link there would
    lead nowhere, and a disabled affordance is worse than no affordance."""
    page = client.get("/dashboard/ui").text

    assert "show me" not in page.lower()


def test_no_sigma_label_is_uppercased_into_summation(client):
    assert "Σ" not in client.get("/dashboard/ui").text
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_gate_control.py -q`
Expected: several failures; no gate markup on the page.

- [ ] **Step 3: Write the macro**

Create `services/backend/ui/templates/shared/_gate.html`:

```jinja
{% macro render(curve, tier, title) %}
{# Thirteen rows, not a slider.

   A drag shows one outcome at a time and hides the other twelve behind a
   gesture; the gate has exactly thirteen legal positions, so every answer
   is on screen at once and the operator reads rather than hunts. It also
   costs no JavaScript, is keyboard-operable with no extra work, and at
   390px it is a list rather than a hit target on a thin axis.

   Each row is a body-less POST, the same shape the theme toggle uses: the
   values ride in the query string so CloudFront's OAC needs no payload
   hash. #}
<div class="c-gate-curve" role="group" aria-label="{{ title }}">
  {% for o in curve %}
  <form method="post" action="/dashboard/ui/gate?tier={{ tier }}&sigma={{ o.sigma }}">
    <button type="submit" class="c-gate-row"
            {% if o.current %}aria-current="true"{% endif %}
            data-bar="{{ (o.width * 20)|round|int }}">
      <span class="c-gate-sigma">{{ "%.2f"|format(o.sigma) }}σ</span>
      <span class="c-gate-bar" aria-hidden="true"></span>
      <span class="c-gate-count">{{ "{:,}".format(o.count) }}</span>
      {% if not o.recommended %}
      <span class="c-gate-note">more false positives measured here</span>
      {% endif %}
    </button>
  </form>
  {% endfor %}
</div>
{% endmacro %}
```

The bar is the one place an enumerated rule is still the right answer:
twenty-one widths of 5% each, 21 rules and about 400 bytes, because the
value is a *bucketed* proportion rather than a measurement. SVG would cost
more markup than the rules cost stylesheet for a row this small.

- [ ] **Step 4: Write the CSS**

Append to `services/backend/ui/static/app.css`:

```css
/* --- the gate control -------------------------------------------------- */

.c-gate-curve { display: grid; gap: 1px; }
.c-gate-row {
  display: grid; grid-template-columns: 4.5rem 1fr 4rem; align-items: center;
  gap: 8px; width: 100%; padding: 4px 8px;
  background: none; border: 0; text-align: left; cursor: pointer;
  font-family: var(--font-mono); font-size: var(--text-xs); color: var(--text);
}
.c-gate-row:hover { background: var(--surface-sunken); }
/* The current position is marked by a bar and a weight, never by colour
 * alone - the same rule the tier ramp follows. */
.c-gate-row[aria-current="true"] {
  font-weight: 700; box-shadow: inset 3px 0 0 var(--accent);
}
.c-gate-bar { height: 8px; background: var(--text-muted); opacity: .4; }
.c-gate-count { text-align: right; font-variant-numeric: tabular-nums; }
.c-gate-note { grid-column: 1 / -1; color: var(--tier-limited); }

/* Bucketed, not measured: 21 steps of 5%. An enumerated rule is the right
 * answer for a bucket and the wrong one for a measurement, which is why the
 * axis uses SVG and this does not. */
.c-gate-bar { inline-size: 0; }
[data-bar="1"] .c-gate-bar { inline-size: 5%; }
[data-bar="2"] .c-gate-bar { inline-size: 10%; }
[data-bar="3"] .c-gate-bar { inline-size: 15%; }
[data-bar="4"] .c-gate-bar { inline-size: 20%; }
[data-bar="5"] .c-gate-bar { inline-size: 25%; }
[data-bar="6"] .c-gate-bar { inline-size: 30%; }
[data-bar="7"] .c-gate-bar { inline-size: 35%; }
[data-bar="8"] .c-gate-bar { inline-size: 40%; }
[data-bar="9"] .c-gate-bar { inline-size: 45%; }
[data-bar="10"] .c-gate-bar { inline-size: 50%; }
[data-bar="11"] .c-gate-bar { inline-size: 55%; }
[data-bar="12"] .c-gate-bar { inline-size: 60%; }
[data-bar="13"] .c-gate-bar { inline-size: 65%; }
[data-bar="14"] .c-gate-bar { inline-size: 70%; }
[data-bar="15"] .c-gate-bar { inline-size: 75%; }
[data-bar="16"] .c-gate-bar { inline-size: 80%; }
[data-bar="17"] .c-gate-bar { inline-size: 85%; }
[data-bar="18"] .c-gate-bar { inline-size: 90%; }
[data-bar="19"] .c-gate-bar { inline-size: 95%; }
[data-bar="20"] .c-gate-bar { inline-size: 100%; }
```

- [ ] **Step 5: Put it on the page, next to the axis**

In `dashboard.py`, build the curve alongside the axis:

```python
    blocked_total = len([r for r in rows if r["z"] is not None])
    curve = gate_curve(bins, tier1_sigma, blocked_total)
```

and pass `curve=curve`. In `dashboard_status.html`, import
`shared/_gate.html` and render it **inside the same panel as the axis**,
under a label saying what moving it does. Not a settings page, not a
separate card: the spec's one non-negotiable is that the explanation and the
control are the same object.

Show it only when `axis_state.gates_armed` is true. A tenant with no model
has nothing to tune and a control that cannot take effect is a promise the
product is not keeping.

- [ ] **Step 6: Run everything and lint**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q --cov=services/backend --cov=services/agent --cov-fail-under=80`
Then: `ruff check services/backend services/agent`
Then check the budget: total CSS under 80,000 bytes.

- [ ] **Step 7: Commit**

```bash
git add services/backend/ui/templates/ services/backend/ui/static/app.css \
        services/backend/ui/dashboard.py services/backend/tests/test_gate_control.py
git commit -m "feat(console): the gate is adjusted on the scale that reports it"
```

---

## Done when

- The full suite passes and coverage stays above 80%.
- `ruff check` is clean.
- Total CSS is under 80,000 bytes.
- The control works with JavaScript disabled: every row is a plain form.
- No request in this plan carries a body.

**Carried forward:** Phase 0's browser confirmation of the htmx config is
still outstanding and this plan does not discharge it.


---

## What the plan got wrong

Written after execution, against the shipped code.

**1. The control cannot be a plain `<form method="post">`.**

Task 3 specified thirteen plain forms and a test asserting `"hx-" not in
control`, reasoning from the theme toggle at `app_shell.html:76`. The theme
toggle is unauthenticated and carries no CSRF dependency. `POST
/dashboard/ui/gate` carries `Depends(verify_csrf)`, and `verify_csrf` reads
the `X-CSRF-Token` **header** against an httponly cookie. A plain form cannot
set a header, and the only way to give it one would be a hidden field, which
is a request body - exactly what ADR-005 forbids behind CloudFront's OAC.

Task 2 had already settled the question from the other side: the route
returns `HX-Redirect`, which only htmx acts on. A plain form would have
landed the operator on a blank 200.

Shipped as thirteen `hx-post` buttons, the same transport as every other
mutation in this console, with the CSRF token inherited from the `hx-headers`
on the console wrapper at `app_shell.html:11`. The position still rides
entirely in the query string, so the POST still has no body.

**2. The console was reading the gate from the wrong item.**

`status_page` took `tier1_sigma` from `stats.tier1_z`, which
`load_model_and_stats` reads off the **Models** item. Task 2 writes the move
to the **Tenants** item, because `save_model` rewrites the Models item every
night and would otherwise revert the operator. The two never met: a customer
could move the gate and the axis, the bands and the curve's own current-position
mark would all still show the old one. The control would have reported that
nothing had happened.

`status_page` now reads the gate of record from Tenants, one small GetItem on
a page that already costs a Query and a model load.

**3. The 24-hour lag between setting a gate and enforcing it was never stated.**

Phase 0 chose to propagate `Tenants.tier1_z` onto the model at the nightly
retrain (`test_per_tenant_thresholds.py`). That is the correct place to store
it, but it means a move does not reach enforcement until the next training
run. Nothing on the screen said so, so the screen would have shown a gate
that was not the gate being applied and made no distinction.

The panel now names both when they differ: what is being enforced right now,
and that the new position starts applying tonight.

Worth revisiting: `assert_tenant_active` already does a `TenantsTable.get` on
**every** authenticated agent request, so threading the gate from there into
`classify()` would make a move take effect on the next telemetry batch at
zero extra RCU. That is a change to the enforcement path and was left out of
this phase deliberately rather than made quietly.

**4. Test pollution from the class-level model cache.**

`ModelManager._cache` is deliberately class-level and survives across tests.
A test that trains a model leaves it loaded for the next test, so
`test_a_tenant_with_no_model_is_not_offered_a_gate_to_move` saw a model and
the armed control. The fixture now clears the cache, which is also what a
cold container looks like.

**5. Minor.** The plan's `_gate.html` used a bare `sigma` glyph in the row
label. Written out as the word instead, for the same reason the incident
screen stopped using it: a stray `text-transform: uppercase` anywhere above
it turns it into the summation sign.
