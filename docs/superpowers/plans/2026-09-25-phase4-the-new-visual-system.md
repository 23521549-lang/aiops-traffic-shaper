# Phase 4 — the new visual system Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace a visual language that read as a generic developer dashboard
with one that reads as a control platform, and put on screen the one thing the
product has never shown: what the customer's agent is actually doing.

**Architecture:** The token architecture already supported two themes, so this
changes values and type, not structure. The new system has one rule that is
not decoration: **on the sigma axis, colour IS distance.** A reader should know
where the trouble is without reading a legend, which is what makes this both
more beautiful and easier to use rather than trading one for the other.

**Tech Stack:** FastAPI, Jinja2, htmx 2.0.4 (vendored), server-rendered SVG,
self-hosted woff2. No client framework, no animation library.

**Design source:** `docs/ui-rebuild/style-directions.html`
**Spec:** `docs/ui-rebuild/05-spec-thiet-ke.md` (unchanged; this serves it)

## Global Constraints

- **Cost stays zero.** Nothing in this phase may add a request. All motion is
  CSS/SVG that runs once at render. Spec §12.3 refuses auto-refresh under 60
  seconds and §12.4 refuses anything real-time: one tab at 5 seconds is 52% of
  the account's whole daily request ceiling, and hitting that ceiling makes
  the platform refuse telemetry for **every** tenant. A dashboard would
  switch off protection. The liveness in this design is in how the page draws
  itself, never in how often it asks.
- **No third-party resources** (§12.9). Fonts are self-hosted woff2 served
  from `/ui/static/`.
- CSP is `default-src 'self'; script-src 'self'; style-src 'self'`. No inline
  `style=` attribute and no inline handler. SVG geometry and presentation
  attributes are not CSS and are unaffected.
- Total CSS under 80,000 bytes. It is at 76,618 at the start of Task 2.
- Hand-written JS under 20,480 bytes. It is at 18,452 and **this phase adds
  none**.
- Every contrast pair in `test_contrast.py` must pass in both themes. Compute
  the palette and prove it before writing it; do not guess and iterate.
- Never colour alone. Every state carries a word as well.
- `prefers-reduced-motion` renders the final state immediately.
- Coverage gate 80%. Run the suite in two parts on this machine (see the
  environment note) and `ruff check` separately.

---

## Task 1: Type and palette — DONE

Shipped before this plan was written, because the palette had to be proved
against `test_contrast.py` before anything could be designed on top of it.

**What shipped**
- `plex-sans-latin.woff2` and `plex-mono-latin.woff2`, 59KB for both, latin
  subset, registered in `_ASSETS`. Plex Sans is variable, so one file covers
  every weight. One family with two voices: words in the sans, every measured
  number and identifier in the mono.
- A palette where the tier ramp is a temperature: teal calm, amber near,
  rose acted-on. Accent is violet and appears only on things that actuate.
- Both themes pass every pair in `test_contrast.py`, computed first.

**What it caught on the way**
- 3,630 bytes of dead CSS: the sigma strip deleted in Phase 1a, the ops Agents
  page deleted in Phase 2, and an old badge system. CSS went 80,357 → 76,618.
- `test_static_assets.py` scanned only templates for `/ui/static/` references,
  so a font referenced from an `@font-face` rule read as served-but-unused.
  That is the referenced-but-not-served defect pointing the other way, and it
  would have argued for deleting a file the console needs. The scan now
  includes stylesheets.
- `.on-ink` in `app.css` duplicates the dark tier ramp, and
  `test_ink_palette.py` exists to stop the two drifting. It caught the drift
  the moment the ramp changed, which is exactly its job.

---

## Task 2: Say what sigma means, where it is first used

The product's whole argument rests on a Greek letter. A reader who does not
already know what it means has to infer it, and the first thing that makes
somebody give up on a security console is being made to translate.

Two additions, both plain language, neither replacing the number.

**Files:**
- Modify: `services/backend/ui/templates/dashboard_status.html`
- Modify: `services/backend/ui/templates/shared/_axis.html`
- Modify: `services/backend/ui/static/app.css`
- Test: `services/backend/tests/test_plain_language.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""A Greek letter does not explain itself.

The product's entire argument rests on one: distance from this tenant's own
normal, in standard deviations. Every screen prints it and no screen has ever
said what it means. A reader who has to translate before they can act is a
reader who stops, and this is a console somebody opens while something is
going wrong.
"""


def test_the_gate_screen_says_what_sigma_means(client, dynamo_resource):
    """In words, before the first number that uses it."""
    page = client.get("/dashboard/ui").text
    at = page.index("c-plain")

    assert "how unusual" in page[at:at + 400].lower()


def test_the_explanation_comes_before_the_axis(client, dynamo_resource):
    ...


def test_the_scale_is_labelled_in_words_at_both_ends(client, dynamo_resource):
    """Ticks read 0 to 6. A reader who does not know the unit cannot tell
    whether 6 is good."""
    page = client.get("/dashboard/ui").text

    assert "like your normal day" in page
    assert "very unlike you" in page


def test_the_words_do_not_replace_the_numbers(client, dynamo_resource):
    """An operator pasting a row into a ticket needs the figure."""
    ...


def test_a_tenant_with_no_model_is_not_taught_a_unit_it_cannot_see(client):
    """Nothing is measured yet, so the scale carries no reading and the
    explanation would be a lesson about nothing."""
    ...
```

Fill each `...` before running.

- [ ] **Step 2 through 4:** run red; add a `c-plain` block to
`dashboard_status.html` above the axis panel and a word-labelled row under the
tick row in `_axis.html`; run green.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(ui): the scale says what it measures, in words"
```

---

## Task 3: Colour is distance, on the axis itself

The tier ramp changed in Task 1, but the axis still paints its three bands as
three flat fills. The design's central idea is that colour is a **continuous**
reading of distance: a source at 3.9 sigma and one at 4.1 sigma are nearly the
same thing, and two flat bands say they are opposites.

A gradient along the axis, with the gates drawn as lines over it, says the
true thing: the gates are where the product *acts*, not where the traffic
*changes*.

**Files:**
- Modify: `services/backend/ui/templates/shared/_axis.html`
- Modify: `services/backend/ui/templates/_grid.html`
- Modify: `services/backend/ui/static/app.css`
- Test: `services/backend/tests/test_colour_is_distance.py` (create)

- [ ] **Step 1: Write the failing test**

```python
def test_the_axis_paints_a_continuous_scale(...):
    """Three flat bands say 3.9 and 4.1 sigma are opposite things. They are
    nearly the same thing, and the gate is a decision laid over them rather
    than a property of the traffic."""
    ...


def test_the_gradient_stops_are_the_tier_tokens_and_not_new_colours(...):
    """A second palette living in an SVG is a palette that drifts."""
    ...


def test_the_gates_are_still_drawn_as_lines_over_it(...):
    ...


def test_a_source_mark_still_carries_its_tier_class(...):
    """Colour gained a second job. It may not lose the first: the mark is
    what a screenshot in a ticket is read from."""
    ...


def test_the_bands_still_carry_their_words(...):
    """WCAG 1.4.1. A gradient is colour alone unless the words stay."""
    ...
```

- [ ] **Step 2 through 5:** as usual, then commit.

```bash
git commit -m "feat(charts): on the axis, colour is the distance"
```

---

## Task 4: What your agent is doing

The BA report's job #1, and the thing no screen has ever answered: a customer
installs an agent on their own server and has no way to see what it is doing
there. The Agents screen says an agent is *reporting*. It does not say what
happens to a decision after that, what tool writes the rule, or whether the
other machines in the fleet got it.

All of it is already known to the product. None of it is drawn.

A four-step strip at the top of the Gate screen, and each step is a real
figure the page already has:

1. **Read** — requests measured in the window, and how many agents are sending
2. **Measured** — distinct sources scored against this tenant's baseline
3. **Past a gate** — how many crossed, split by tier
4. **Written** — how many agents have actually collected and applied it

Step 4 is the one that matters and the one principle 1.5 has been asking for
since Phase 1d: it is `reach()` drawn instead of written.

**Files:**
- Create: `services/backend/ui/templates/_lifecycle.html`
- Modify: `services/backend/ui/templates/dashboard_status.html`
- Modify: `services/backend/ui/dashboard.py`
- Modify: `services/backend/ui/static/app.css`
- Test: `services/backend/tests/test_agent_lifecycle_strip.py` (create)

**Interfaces:**
- Consumes: `measured`, `rows`, `health`, and `reach()` — all already in
  `protection_status`'s context. **No new query.**

- [ ] **Step 1: Write the failing test**

```python
def test_the_strip_shows_all_four_steps(client, dynamo_resource):
    ...


def test_every_figure_comes_from_something_already_read(client, dynamo_resource, monkeypatch):
    """The strip is worth nothing if it costs a query. Every number it shows
    is already on this page for another reason."""
    ...


def test_the_last_step_says_how_many_agents_have_it(client, dynamo_resource):
    """Principle 1.5, drawn. The backend deciding and the customer's nginx
    changing are two different events and this is where that becomes
    visible."""
    ...


def test_a_tenant_with_no_agent_gets_no_pipeline_to_look_at(client):
    """Nothing is flowing. A strip of zeroes implies a system that is
    running and idle, which is the opposite of the truth."""
    ...


def test_the_strip_names_the_tool_that_writes_the_rule(client, dynamo_resource):
    """nginx or iptables. "Enforced" is an abstraction; the customer has to
    know which file on their own machine changed."""
    ...


def test_the_motion_costs_no_request(client, dynamo_resource):
    """The pulse is CSS. A console that polls turns off protection for every
    tenant on the platform, which is the whole reason spec 12.3 exists."""
    page = client.get("/dashboard/ui").text

    assert "hx-trigger" not in page
    assert "setInterval" not in page
```

- [ ] **Step 2 through 5:** as usual, then commit.

```bash
git commit -m "feat(console): the customer can see what their agent is doing"
```

---

## Self-review

**Does this drift from the original idea?** No, and the check is worth making
explicitly. The spec's one non-negotiable is that the explanation and the
control are the same object, and that the sigma axis is the primary object.
Task 3 strengthens that by making the axis carry more meaning per pixel. Task
4 adds a strip above it that answers a different question - *is the machinery
running* - which the BA report lists as job #1 and which no screen has ever
answered. Neither replaces the axis.

**Does this add technical debt?** Task 1 removed 3,630 bytes of dead CSS and
closed a real hole in the asset test. Tasks 2 to 4 add no JavaScript, no
query, no dependency, and no new asset.

**What could go wrong.** The gradient in Task 3 is the one place where a
second palette could take root inside an SVG. The test pins that its stops are
the tier tokens. And Task 4's strip is the kind of thing that grows into a
dashboard of vanity figures; the test that every number is already on the page
for another reason is what stops that.
