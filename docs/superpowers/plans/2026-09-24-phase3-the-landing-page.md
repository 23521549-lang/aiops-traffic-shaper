# Phase 3 — the landing page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the landing page one next action that actually exists, stop its
most prominent chart presenting fabricated data as a measurement, keep the
section that makes the page worth trusting, and close the accessibility
contract the console's own time axis does not yet meet.

**Architecture:** This page reads no DynamoDB and must keep reading none: that
property is what lets it sit behind a CloudFront cache behaviour and cost
nothing. So the one action it offers cannot be a write. Access is granted by
one operator, by hand, and the page says so - which is the same asset the rest
of the page trades on.

**Tech Stack:** FastAPI, Jinja2, server-rendered SVG, no client framework.

**Spec:** `docs/ui-rebuild/05-spec-thiet-ke.md` §7, §8, §1.4

## Global Constraints

- No em dash may appear in any rendered UI string.
- CSP is `default-src 'self'; script-src 'self'; style-src 'self'`.
- **The landing page reads no DynamoDB.** Not a preference: it is what makes
  the page cacheable and free, and a test pins it.
- **No anonymous write path.** A request-access form that stores anything is a
  write endpoint on a public URL against a 20 WCU account budget, which is an
  abuse surface the product has no rate limiting for (ADR-002's accepted
  residual risk).
- Total CSS across `services/backend/ui/static/*.css` stays under 80,000
  bytes. It is at 78,603, so this phase has about 1,400 bytes.
- Principle 1.4: no sentence and no picture may be contradicted by actual
  behaviour.
- Coverage gate: 80%. Run `ruff check` separately from pytest, and run the
  suite in two parts - the machine has been running low on memory.

---

## Task 1: The hero chart presents fabricated data as a measurement

`_HERO_SHAPE` in `public.py` is forty-eight hardcoded floats. The page renders
them captioned **"24 hours of traffic in standard deviations from this site's
own normal"** and, below that, **"One source reached 4.6 sigma and was slowed;
one reached 5.4 sigma and was blocked."**

Both sentences describe an event. No such event happened, no such site exists,
and nothing on the page says so. This is the most prominent element on the
most public page in the product, and it is the clearest principle 1.4
violation left anywhere in it: the rest of the page spends its credibility
admitting limits, and this spends it the other way.

The shape itself is worth keeping - it shows the reader what the units look
like, which is the page's actual job. What has to change is that it says what
it is.

**Files:**
- Modify: `services/backend/ui/public.py` (`_HERO_SHAPE`, the captions)
- Modify: `services/backend/ui/templates/landing.html`
- Test: `services/backend/tests/test_landing_honesty.py` (create)

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_landing_honesty.py`:

```python
"""The page that spends its credibility admitting limits.

`_HERO_SHAPE` is forty-eight hardcoded floats, and the page captions them as
twenty-four hours of a real site's traffic with two specific enforcement
events in it. No such site exists and no such event happened.

Every other claim on this page has been checked against the code at some
point - `test_landing_claims.py` exists because four of them had rotted into
fiction. The picture was never checked, because a chart is not a sentence.
"""


def test_the_example_chart_says_it_is_an_example(page):
    """Not buried in small print under it: on the chart, where a reader who
    reads nothing else still sees it."""
    ...


def test_the_caption_does_not_describe_events_that_did_not_happen(page):
    """"One source reached 4.6 sigma and was slowed" is a sentence about a
    thing that occurred. Nothing occurred."""
    ...


def test_the_shape_is_still_shown(page):
    """Removing it would be the easy answer and the wrong one: showing a
    reader what these units look like is the page's actual job."""
    ...


def test_no_figure_on_the_page_is_presented_as_a_customer_measurement(page):
    """The product has never published a customer's numbers and must not
    start on the page that argues it measures yours rather than somebody
    else's."""
    ...
```

Fill each `...` before running.

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Say what it is**

Rename `_HERO_SHAPE` to `_EXAMPLE_SHAPE` and rewrite both captions so they
describe an illustration rather than an event. The chart keeps a visible
"Example" marker in the rendered output, not only in the alt text.

- [ ] **Step 4: Run green, commit**

```bash
git commit -m "fix(landing): the example chart says it is an example"
```

---

## Task 2: One next action, and it exists

Spec §7: the page's job is to convince a stranger this product measures their
own site, and to give them **exactly one next action that actually exists**.

Today the hero offers "Install the agent", and the install section says "Sign
in, open Agents, and add one." There is no self-service signup anywhere in the
product: tenants are created by an operator through
`POST /admin/v1/tenants`, and there is no route a stranger can reach to ask
for one. So the page's primary call to action is an instruction its reader
cannot follow, which is the failure the spec names explicitly - a button
pointing at a signup page that does not exist is worse than admitting the
constraint.

The action is **request access**, routed to the operator, and the page states
plainly that it is invite-only and that one person reads the requests.

It must not be a write. The page reads no DynamoDB and stores nothing, which
is what makes it cacheable and free, and a public POST that stores anything is
an abuse surface on a 20 WCU budget with no rate limiting in front of it. A
`mailto:` routes to the operator, stores nothing, adds no endpoint, and is
honest about what happens next: a person reads it.

**Files:**
- Modify: `services/backend/core/config.py` (`access_request_email`)
- Modify: `services/backend/ui/public.py`
- Modify: `services/backend/ui/templates/landing.html`
- Test: `services/backend/tests/test_landing_access.py` (create)

**Interfaces:**
- Produces: `settings.access_request_email: str`, passed into the template.

- [ ] **Step 1: Write the failing test**

```python
def test_the_primary_action_is_one_a_stranger_can_take(page):
    """There is no self-service signup. "Install the agent" is an
    instruction whose first step is signing in to an account nobody can
    create."""
    ...


def test_the_page_says_it_is_invite_only(page):
    """Spec 7. The page spends its credibility admitting constraints, and
    this is the constraint a reader hits first."""
    ...


def test_the_page_says_a_person_reads_the_requests(page):
    """One operator, by hand. A reader who expects an instant provisioning
    email and waits for one has been misled by omission."""
    ...


def test_the_request_action_stores_nothing(page):
    """A public POST that writes is an abuse surface on a 20 WCU budget with
    no rate limiting in front of it, and it would make the page uncacheable
    besides."""
    ...


def test_sign_in_is_still_offered_for_people_who_have_an_account(page):
    ...


def test_the_install_instructions_are_still_there(page):
    """They are what happens after access is granted, and a reader deciding
    whether to ask wants to know what they are agreeing to. They stop being
    the call to action; they do not stop existing."""
    ...


def test_nothing_on_the_page_promises_a_signup_that_does_not_exist(page):
    ...
```

- [ ] **Step 2 through 4:** run red, add the setting, rewrite the hero call to
action and the install lead-in, run green.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(landing): one next action, and it is one that exists"
```

---

## Task 3: The two properties that must survive any rewrite

Spec §7 names both, and both are the kind of thing a later tidy-up removes
without noticing.

**"What it doesn't do yet" stays.** A quiet rewrite that drops it turns this
into every other security vendor's page. It is section S6 today.

**The page reads no DynamoDB.** That is what lets it sit behind a cache
behaviour and cost nothing, and it is one careless `Depends(get_dynamo_resource)`
away from being false.

Neither has a test. Both get one.

**Files:**
- Test: `services/backend/tests/test_landing_honesty.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
def test_the_page_still_says_what_it_cannot_do(page):
    """A rewrite that quietly drops this section turns the page into every
    other security vendor's page. It is the only thing on it a competitor
    would not print."""
    ...


def test_the_limits_section_is_specific_rather_than_a_gesture(page):
    """"We are always improving" is not this section. It names features the
    product does not have."""
    ...


def test_the_landing_page_reads_no_dynamodb(monkeypatch):
    """Not a preference. It is what lets this page sit behind a cache
    behaviour and cost nothing, and it is one careless Depends away from
    being false."""
    ...


def test_the_landing_route_takes_no_resource_dependency():
    """The mechanism behind the test above, checked directly so the failure
    names the cause rather than a symptom."""
    import inspect

    from services.backend.ui import public

    assert "get_dynamo_resource" not in inspect.getsource(public.landing)
```

- [ ] **Step 2 through 4:** run it, fix anything it finds, commit.

```bash
git commit -m "test(landing): pin the two properties a rewrite would quietly drop"
```

---

## Task 4: The accessibility contract the time axis does not meet

Spec §8 is explicit about the shape of the parallel table for the time axis:
**24 rows by 3 columns - hour, sources past the gate, peak sigma.** Not a
24 by 13 matrix of 312 cells, which nobody wants read aloud, and not an
aggregate either.

Phase 1d shipped a four-row summary: hours in the window, hours with
telemetry, hours with a source past the gate, busiest single reading. That
answers less than the chart does. A screen reader user can learn that three
hours had a source past the gate and cannot learn which three, which is the
question the picture answers at a glance.

**Files:**
- Modify: `services/backend/ui/charts.py` (`Grid` gains per-row figures)
- Modify: `services/backend/ui/templates/_grid.html`
- Test: `services/backend/tests/test_history_grid.py` (extend)
- Test: `services/backend/tests/test_history_screen.py` (extend)

**Interfaces:**
- Produces: `GridRow.past_gate: int` and `GridRow.peak_sigma: float | None`.

- [ ] **Step 1: Write the failing test**

```python
def test_each_row_carries_the_two_figures_the_table_needs(...):
    """Spec 8: hour, sources past the gate, peak sigma. Computed on the row
    rather than in the template, because a Jinja expression nobody can read
    is how the summary got wrong in the first place."""
    ...


def test_an_hour_with_no_reading_has_no_peak(...):
    """None, not zero. Zero sigma is a measurement meaning "exactly
    normal", and an hour with no telemetry measured nothing."""
    ...


def test_the_peak_is_the_furthest_out_and_not_the_last(...):
    ...


def test_the_table_has_one_row_per_hour(client, ...):
    ...


def test_the_table_is_not_a_matrix_of_every_bin(client, ...):
    """312 numbers read aloud answer no question anyone has."""
    ...
```

- [ ] **Step 2 through 4:** run red, compute the two figures in
`history_grid`, rewrite the `sr-only` table in `_grid.html`, run green.

- [ ] **Step 5: Run everything in two parts, lint, check the budget, commit**

```bash
PYTHONPATH=. python -m pytest services/backend/tests/ -q \
  --ignore=services/backend/tests/test_training_poisoning.py
PYTHONPATH=. python -m pytest services/backend/tests/test_training_poisoning.py \
  services/agent/tests/ -q
ruff check services/backend services/agent
wc -c services/backend/ui/static/*.css
git commit -m "fix(a11y): the time axis carries the table spec 8 asks for"
```

---

## Self-review

**Spec coverage.** §7 has four requirements: convince a stranger the product
measures their own site (already the page's whole argument, and Task 1 stops
it lying while doing so), exactly one next action that exists (Task 2), say
plainly that it is invite-only with one operator (Task 2), keep "what it
doesn't do yet" (Task 3), and read no DynamoDB (Task 3). §8's parallel-table
shape is Task 4; its other clauses - keyboard reachability, never colour
alone, contrast measured per surface, `tabindex="0"` and `role="region"` on
scroll regions - already ship and are already tested, so they are not
re-litigated here.

**Type consistency.** `GridRow` gains two fields in Task 4, read only by
`_grid.html`. `settings.access_request_email` in Task 2 is read only by
`public.landing`.

**Known risks, stated rather than designed around.** A `mailto:` link exposes
an address to scrapers. That address is the operator's and is already the
product's public contact route; the alternative is an anonymous write endpoint
on a free-tier budget with no rate limiting, which is worse. Task 1 changes
marketing copy, which is the one kind of change in this rebuild with no
mechanical way to verify taste - the tests pin the factual claims and not the
prose.
