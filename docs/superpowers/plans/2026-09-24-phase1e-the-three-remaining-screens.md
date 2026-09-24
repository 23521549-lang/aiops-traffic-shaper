# Phase 1e — the three remaining screens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the customer console. Rename the allowed list to one canonical
path and make it a register that shows what allowing costs, teach the Agents
screen to tell "sending telemetry" apart from "able to write rules", and open
the Model screen onto the baseline and the staging model nothing has ever
read.

**Architecture:** Three screens, three separate honesty problems. The allowed
list currently lists entries and never says that each one is removed from the
tenant's own baseline permanently, which inflates sigma for everything else.
The Agents screen reports a green "Reporting" for an agent that found no
enforcement backend and is protecting nothing. The Model screen does not
exist. None of the three needs a new query on a hot path; the one new number
is counted inside a nightly job that already walks the data.

**Tech Stack:** FastAPI, Jinja2, htmx 2.0.4 (vendored), server-rendered SVG,
DynamoDB single-table.

**Spec:** `docs/ui-rebuild/05-spec-thiet-ke.md` §4.5, §4.6, §4.7, §4.8, §1.4

## Global Constraints

- No em dash may appear in any rendered UI string.
- CSP is `default-src 'self'; script-src 'self'; style-src 'self'`. No inline
  `style=` attribute, no inline `<script>`.
- Every mutation is a body-less POST with its values in the query string, sent
  by htmx so the CSRF token is inherited from the console wrapper's
  `hx-headers`. A POST body behind CloudFront's OAC needs
  `x-amz-content-sha256`, which only `signed-post.js` can compute, and that
  file has one caller and must keep one.
- `templates/shared/` is for macros BOTH surfaces render. A console-only macro
  belongs in `templates/`. `test_shared_chart_styles.py` enforces this.
- Total CSS across `services/backend/ui/static/*.css` stays under 80,000
  bytes. It is at 77,674 at the start of this phase, so this phase has about
  2,300 bytes. Reuse existing classes; do not add a new component where
  `c-panel`, `c-table`, `c-kv` or `c-state` will do.
- Principle 1.4: the interface may only say true things. No sentence may be
  contradicted by actual behaviour. Where a figure cannot be measured, say so
  rather than showing a plausible one.
- Coverage gate: 80%. Run `ruff check` as a separate command from pytest.

---

## Task 1: One path for the allowed list

Spec §4.5 opens by requiring the rename and then requiring that exactly one
path exist: "update every link that points at it rather than letting two URLs
live". Two live URLs for one screen is how a nav item and a command palette
entry end up pointing at different handlers a year later.

There are thirteen references today across five routes, four templates and the
palette. A grep-driven rename is the whole task, plus a test that stops the
old name coming back.

**Files:**
- Modify: `services/backend/ui/dashboard.py` (5 routes)
- Modify: `services/backend/ui/templates/app_shell.html`
- Modify: `services/backend/ui/templates/dashboard_status.html` (5 links)
- Modify: `services/backend/ui/templates/dashboard_whitelist.html` → rename to
  `dashboard_allowed.html`
- Modify: `services/backend/ui/templates/_whitelist_table.html` → rename to
  `_allowed_table.html`
- Modify: `services/backend/ui/templates/_palette.html`
- Test: `services/backend/tests/test_allowed_list.py` (create)

**Interfaces:**
- Consumes: `add_whitelist`, `remove_whitelist`, `list_whitelist` from
  `api/routes/dashboard.py` — the JSON API paths `/dashboard/v1/whitelist`
  do NOT change. They are a versioned contract with the agent CLI, and the
  rename is a console concern.
- Produces: `/dashboard/ui/allowed`, `/dashboard/ui/allowed/{ip}` (POST and
  DELETE), `/dashboard/ui/allowed/bulk`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_allowed_list.py` with a client fixture in
the shape used by `test_gate_control.py`, and:

```python
def test_the_allowed_list_lives_at_one_path(client):
    assert client.get("/dashboard/ui/allowed").status_code == 200


def test_the_old_path_is_gone_rather_than_redirecting(client):
    """Spec 4.5: one path exists. A redirect is a second URL that keeps
    working, and the nav item and the command palette drift apart behind
    it."""
    assert client.get("/dashboard/ui/whitelist").status_code == 404


def test_nothing_in_the_console_still_links_to_the_old_path():
    """The reason the rename is a task rather than a sed: thirteen
    references across five routes, four templates and the palette."""
    from pathlib import Path

    ui = Path("services/backend/ui")
    for path in list(ui.rglob("*.html")) + list(ui.rglob("*.py")):
        assert "/dashboard/ui/whitelist" not in path.read_text(encoding="utf-8"), path


def test_the_json_api_path_is_untouched(client):
    """`/dashboard/v1/whitelist` is a versioned contract the agent CLI
    consumes. The rename is a console concern and must not reach it."""
    assert client.get("/dashboard/v1/whitelist").status_code == 200


def test_allowing_from_the_protection_screen_still_works(client, dynamo_resource):
    ...  # POST /dashboard/ui/allowed/10.0.0.7?back=status, assert HX-Redirect


def test_the_bulk_action_still_works(client, dynamo_resource):
    ...  # POST /dashboard/ui/allowed/bulk?ip=10.0.0.7&ip=10.0.0.8
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_allowed_list.py -q`

- [ ] **Step 3: Rename**

```bash
git mv services/backend/ui/templates/dashboard_whitelist.html \
       services/backend/ui/templates/dashboard_allowed.html
git mv services/backend/ui/templates/_whitelist_table.html \
       services/backend/ui/templates/_allowed_table.html
```

Then replace `/dashboard/ui/whitelist` with `/dashboard/ui/allowed`
everywhere it appears under `services/backend/ui/`, and the two template
names at their render sites. Leave `/dashboard/v1/whitelist`,
`WhitelistTable`, `add_whitelist` and `list_whitelist` alone: the storage and
the JSON contract keep their names, and only the console path changes.

`hx_return`'s `_WHITELIST_RETURNS` map contains the destination path. Check it.

- [ ] **Step 4: Run the whole suite**

Other test files reference the old path. They are testing real behaviour
through a URL that no longer exists, so update the URL and leave the
assertions alone.

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ -q`

- [ ] **Step 5: Commit**

```bash
git add services/backend/ui/ services/backend/tests/
git commit -m "refactor(console): the allowed list has one path, and it is /allowed"
```

---

## Task 2: What allowing actually costs

Spec §4.5, and the sharpest thing in it: **allowing an IP removes it from your
own baseline, permanently.** A customer who allows each CDN address during an
incident is deleting their largest traffic source from the definition of
normal. That inflates sigma for everything that remains and causes the next
false block. Nothing in the product says this today.

The mechanism is real and already implemented:
`collect_training_vectors(..., exclude_ips=...)` skips every whitelisted IP's
buckets, and `docs/architecture.md` has always said the whitelist is excluded
from mitigation *and* from training.

So the honest figure is countable exactly where the exclusion happens, inside
a nightly job that is already walking every bucket. Nothing is estimated and
nothing new is queried.

**Files:**
- Modify: `services/backend/ml/feature_engineering.py`
  (`collect_training_vectors` returns what it dropped)
- Modify: `services/backend/ml/training.py` (`train_and_save` records it)
- Modify: `services/backend/schemas/model_status.py` / `ModelMetadata`
- Modify: `services/backend/ui/dashboard.py` (`allowed_page`)
- Modify: `services/backend/ui/templates/dashboard_allowed.html`
- Test: `services/backend/tests/test_allowed_cost.py` (create)

**Interfaces:**
- Consumes: `ModelsTable.get_metadata(tenant_id, stage_version)` — projected,
  so this costs 0.5 RCU and never fetches the 238KB blob.
- Produces: `collect_training_vectors(...) -> tuple[list[list[float]], dict]`
  where the dict is `{"used": int, "excluded_whitelist": int,
  "excluded_flagged": int}`; `ModelMetadata.excluded_whitelist_buckets: int`
  and `.training_buckets: int`.

**A note the implementer must not skip:** changing
`collect_training_vectors`'s return type breaks its callers. Find them all
before writing the implementation, and prefer an added out-parameter or a
second function over a silent tuple change if there are more than two.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_allowed_cost.py`:

```python
"""What allowing an IP costs, said in the tenant's own numbers.

Allowing a source removes it from the definition of normal, permanently.
A customer clearing each CDN address during an incident is deleting their
largest traffic source from their own baseline, which inflates sigma for
everything that remains and produces the next false block. The product does
this today and says nothing about it, which is the failure principle 1.4
exists to catch.

The figure is counted where the exclusion happens, inside a nightly job
already walking every bucket. Nothing here is estimated.
"""


def test_the_retrain_counts_what_the_whitelist_removed(dynamo_resource):
    """Two buckets, one from an allowed IP. The baseline is built from one
    of them and the count says so."""
    ...


def test_a_flagged_bucket_is_counted_separately(dynamo_resource):
    """Excluded for a different reason and it must not be reported as the
    cost of allowing: one is the customer's choice and the other is the
    system defending itself."""
    ...


def test_the_count_reaches_the_model_item(dynamo_resource):
    ...  # train_and_save, then get_metadata, assert excluded_whitelist_buckets


def test_a_tenant_with_no_allowed_ips_reports_zero_not_nothing(dynamo_resource):
    """Zero is a measurement. Absent reads as "we do not know", and the
    screen has to tell those apart."""
    ...


def test_the_screen_states_the_mechanism_not_just_the_number(client, dynamo_resource):
    """A percentage with no explanation is a statistic. The sentence that
    matters is why removing traffic from the baseline moves everything
    else."""
    page = client.get("/dashboard/ui/allowed").text

    assert "baseline" in page.lower()
    assert "permanently" in page.lower()


def test_a_model_trained_before_this_existed_says_so(client, dynamo_resource):
    """No count on the item. "Not measured yet" and "zero" are different
    claims and the screen may not merge them."""
    ...


def test_the_screen_shows_when_entries_were_added(client, dynamo_resource):
    """The pattern the spec describes is a cluster added inside one
    incident. A list sorted by address hides exactly that."""
    ...


def test_the_register_names_who_added_each_entry(client, dynamo_resource):
    """Spec 4.5: IP, who added it, when, and why. `added_by` has been
    written since Phase 0 and the screen showed a reason and no actor."""
    ...
```

Fill each `...` with a real body before running. A test whose body is an
ellipsis passes and asserts nothing.

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Count the exclusions where they happen**

In `collect_training_vectors`, keep two counters beside the two `continue`
statements that already exist, and return them. Wire the whitelist count into
`ModelMetadata` and therefore onto the model item, which `get_metadata`
already projects.

- [ ] **Step 4: Make the screen a register rather than a list**

`dashboard_allowed.html` gains, above the table:

- the sentence that names the mechanism, in the tenant's own terms;
- the measured share of buckets the baseline left out, or a plain statement
  that it has not been measured yet;
- entries ordered newest first, so a cluster added during one incident reads
  as a cluster.

and the table gains the `added_by` column the data has always carried.

- [ ] **Step 5: Run everything, lint, commit**

```bash
PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q \
  --cov=services/backend --cov=services/agent --cov-fail-under=80
ruff check services/backend services/agent
git commit -m "feat(console): the allowed list says what allowing costs the baseline"
```

---

## Task 3: Reporting is not protecting

Spec §4.6: "An agent that finds no enforcement backend still reports happily
and protects zero, and the console shows Reporting in green."

`detect_adapters()` already returns every adapter that reports itself
available, and an empty list is a legitimate outcome: no nginx, no iptables,
nothing to write a rule with. The agent then sends telemetry forever and
enforces nothing, and every screen in the product calls it healthy.

Nothing reports that list to the backend. `TelemetryBatch` carries `logs` and
nothing else.

**Files:**
- Modify: `services/backend/schemas/telemetry.py` (`TelemetryBatch.enforcers`)
- Modify: `services/backend/api/routes/agent.py` (record it on the touch)
- Modify: `services/backend/core/tables.py` (`AgentsTable.touch`)
- Modify: `services/agent/runner.py` (send it)
- Modify: `services/backend/ui/presenters.py` (`agent_state`)
- Modify: `services/backend/ui/templates/dashboard_agents.html`
- Test: `services/backend/tests/test_agent_can_enforce.py` (create)

**Interfaces:**
- Consumes: `detect_adapters()` and `EnforcementAdapter.name` from
  `services/agent/enforcer/__init__.py`.
- Produces: `TelemetryBatch.enforcers: list[str] = []`, an `enforcers`
  attribute on the Agents item, and `agent_state(...)["can_enforce"]` plus
  `["enforcers_label"]`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_agent_can_enforce.py`:

```python
"""Reporting is not protecting.

detect_adapters() returning an empty list is a legitimate outcome: no nginx,
no iptables, nothing on the machine that can write a rule. The agent then
sends telemetry forever, the backend scores it, decisions are written, and
nothing enforces them. Every screen in the product calls that agent healthy,
in green.

This is the cleanest example of principle 1.4 in the product: a true sentence
("reporting") standing in for a false one ("protected").
"""


def test_an_agent_that_reports_its_enforcers_is_recorded_as_able_to_enforce():
    ...


def test_an_agent_reporting_no_enforcers_is_reporting_and_not_protecting():
    ...


def test_an_agent_that_has_never_said_is_neither_confirmed_nor_denied():
    """Agents running the version before this field existed. "We have not
    been told" is not "it cannot enforce", and marking every old agent as
    broken on upgrade day would be the louder wrong answer."""
    ...


def test_the_screen_separates_the_two_states():
    ...


def test_the_fleet_summary_does_not_call_a_non_enforcing_fleet_healthy():
    ...


def test_the_enforcer_names_are_bounded_rather_than_free_text():
    """This is written onto an item and rendered. It is a closed set of
    adapter names, so it is validated as one rather than stored as given."""
    ...


def test_recording_the_enforcers_costs_no_extra_write():
    """AgentsTable.touch already fires on every authenticated agent request.
    A second write per telemetry batch would be a real cost against a 20 WCU
    account budget, for a field that changes about never."""
    ...
```

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Have the agent say what it can do**

`services/agent/runner.py` sends `enforcers=[a.name for a in adapters]` on the
telemetry batch. Bounded: the names come from the adapter classes, not from
configuration.

- [ ] **Step 4: Record it on the write that already happens**

`AgentsTable.touch` writes `last_seen_at` on every authenticated agent
request. Fold `enforcers` into that same update expression. No second write.

- [ ] **Step 5: Split the two states on the screen**

`agent_state` gains `can_enforce` with three values, not two: confirmed,
confirmed-absent, and not-reported. The Agents table shows both columns, and
the page summary stops saying "N reporting" as though that were the answer.

- [ ] **Step 6: Run everything, lint, commit**

```bash
git commit -m "feat(agents): an agent that can write no rules is not protecting anything"
```

---

## Task 4: The Model screen, and the honest answer to job #4

Spec §4.7 and §4.8. The model screen shows the seven-feature baseline ("this
is the shape of your normal"), the training state, and the **staging model**,
which is written every night even when promotion is refused - deliberately,
because it is evidence - and which no screen has ever read.

§4.8 is the part that takes discipline. "What did you do for me this week"
cannot be answered: nothing records requests actually refused, because the
agent enforces locally and does not report back. The screen may show the
decision count and **must label it as exactly that**. Letting it read as
"requests blocked" merges two different quantities, which is a principle 1.4
violation with a number attached.

**Files:**
- Create: `services/backend/ui/templates/dashboard_model.html` (exists; rewrite)
- Modify: `services/backend/ui/dashboard.py` (`model_page`)
- Modify: `services/backend/ui/presenters.py` (`baseline_rows`)
- Test: `services/backend/tests/test_model_screen.py` (create)

**Interfaces:**
- Consumes: `ModelsTable.get_metadata(tenant_id, stage_version)` for both
  `"production"` and `"staging"` — two projected GetItems, about 1 RCU total,
  on a screen nobody opens in a loop.
- Consumes: `FEATURE_LABELS` and `_figure` from `presenters.py`.
- Produces: `presenters.baseline_rows(means, stds) -> list[dict]` with
  `label`, `normal`, `name`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_model_screen.py`:

```python
def test_the_screen_shows_the_shape_of_normal(client, dynamo_resource):
    """Seven rows, each naming what this tenant's ordinary traffic looks
    like on that dimension. This is the sentence the product is built to be
    able to say."""
    ...


def test_the_staging_model_is_shown_rather_than_only_written(client, dynamo_resource):
    """It is written every night even when promotion is refused, on purpose,
    because it is evidence. No screen has ever read it, so the evidence has
    never been evidence to anyone."""
    ...


def test_a_refused_promotion_says_why_rather_than_showing_two_versions(client, dynamo_resource):
    ...


def test_a_tenant_with_no_model_is_told_what_has_to_happen(client):
    ...


def test_the_decision_count_is_labelled_as_decisions_not_as_requests(client, dynamo_resource):
    """Spec 4.8. Nothing records requests actually refused - the agent
    enforces locally and does not report back. These are two different
    quantities and merging them is principle 1.4 with a number attached."""
    page = client.get("/dashboard/ui/model").text

    assert "requests blocked" not in page.lower()
    assert "decisions" in page.lower()


def test_the_screen_says_plainly_what_it_cannot_tell_you(client, dynamo_resource):
    """Job number four, named in the BA report and unanswerable. Saying so
    beats a number that looks like the answer."""
    ...


def test_the_model_screen_does_not_fetch_the_blob(client, dynamo_resource):
    """The model item is about 238KB, roughly 30 RCU against a table
    provisioned at 2, to print a version string. get_metadata projects."""
    ...
```

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3 through 5:** build `baseline_rows`, rewrite the template, wire
the route to read both stages, and state the §4.8 limit in words on the
screen.

- [ ] **Step 6: Run everything, lint, check the budget, commit**

```bash
PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q \
  --cov=services/backend --cov=services/agent --cov-fail-under=80
ruff check services/backend services/agent
wc -c services/backend/ui/static/*.css
git commit -m "feat(console): the shape of your normal, and the question we cannot answer"
```

---

## Self-review

**Spec coverage.** §4.5 is Tasks 1 and 2 - the rename and the one-path rule in
Task 1, the register and the cumulative cost in Task 2. §4.6 is Task 3; its
other half, the setup loop closing on the first measurement, already ships in
the Agents screen's `created` panel and the axis's `day_one` state, so it is
asserted rather than rebuilt. §4.7 and §4.8 are Task 4.

**Type consistency.** `collect_training_vectors` changes return type in Task 2
and its callers must be found first - the plan says so explicitly because this
is the one place in the phase where a silent breakage is possible.
`agent_state` gains keys in Task 3 and is read by one template.
`baseline_rows` in Task 4 returns the same row shape `decompose` does, minus
the per-source columns, so the existing `c-why` styling applies unchanged.

**Known risks, stated rather than designed around.** Task 1 deletes a URL that
may be bookmarked; the spec requires exactly that, and the product is
invite-only with no external inbound links. Task 2's figure only appears after
the next retrain, so every existing tenant sees "not measured yet" until
then - which the tests pin as a distinct state rather than as zero. Task 3
adds a field to the telemetry batch, so an agent older than the backend sends
nothing and must render as "not reported" rather than as broken.


---

## What the plan got wrong

Written after execution, against the shipped code.

**1. The same defect a fifth, sixth and seventh time: a field written,
declared, and never carried off the row.**

The plan anticipated none of these and every task hit one.

- `list_whitelist` dropped `added_by`, so "who let this address in" had no
  answer on any screen, although it has been written from the verified token
  since Phase 0.
- `AgentSummary` did not declare `enforcers`, and its closed field list is
  deliberate (it is what keeps `api_key_hash` server-side), so the field had
  to be named explicitly.
- `model_status` dropped `feature_means` and `feature_stds`, which
  `get_metadata` already projects - so the one sentence this product exists to
  be able to say, "here is the shape of your normal", had nowhere to be said.

Counting the two found in Phase 1c and 1d, that is five. Every one passed
every test at the time, because unit tests construct the model by hand and
never touch the reader, and integration tests assert on what the screen shows,
and the screen showed nothing because the field never arrived. The defect is
invisible from both ends.

`services/backend/tests/test_fields_survive_the_read_path.py` now tests the
seam directly: write through the real writer, read through the real reader,
assert the value survived. Deliberately a list, so adding a field to a stored
item is a decision to add a line there too.

**2. `collect_training_vectors`'s return type.**

The plan warned to find the callers first, which was right: one production
caller, four test call sites. It shipped as a `TrainingSet` dataclass rather
than a tuple, so `len()` still works and the counts have names.

**3. `touch` could not carry the enforcer list.**

The plan said to fold `enforcers` into `AgentsTable.touch`, on the argument
that touch already fires on every authenticated request. It does - inside
`agent_auth`, which authenticates before anything has parsed the request body
and therefore cannot know this value.

Shipped instead as `authenticated_agent`, a dependency returning the whole
item, with `agent_auth` as a thin wrapper over it. FastAPI caches a
dependency's result within a request, so the route learns the currently stored
value for free, and `set_enforcers` writes only when it has changed - which is
about never. Steady state costs nothing.

**4. Absent and empty are different, and `dict.get(k, [])` merges them.**

The first version compared the reported list against `agent.get("enforcers",
[])`, so an agent reporting "I can enforce with nothing" for the first time
looked like no change at all and was never written. That is precisely the case
the whole field exists for. It now compares against a sentinel.

**5. Two test stubs broke on signatures rather than on behaviour.**

`train_that_breaks_for_one` had a fixed signature and started failing when the
real function gained an argument, in a file about isolation between tenants.
`test_authenticating_an_agent_touches_it` called `agent_auth` directly. Both
fixed at the test, not by bending the code around them.

**6. The Model screen was showing every tenant somebody else's gate.**

Not in the plan at all, found while writing the tests: `4.0σ` and `5.0σ` were
written into the template and the route read the module constants. A customer
who had moved their gate in Phase 1b saw the shipped default on the one page
that claims to explain their own model.

**7. Spec 4.8 needed a change on the History screen, not only the Model
screen.** The headline read "412 blocked · 39 slowed", which beside a traffic
chart is a claim about requests. It now names the quantity: "451 decisions
(412 to block, 39 to slow)". The first version of that test passed on a table
column header, which is exactly the kind of accident it was written to catch.

## Still outstanding after this phase

- Two of spec 2.5's four audited actions remain unwired: tenant
  suspend/reactivate and agent key mint/revoke, both in the operations console
  (Phase 2).
- ADR-007's htmx configuration still needs confirming in a real browser.
- **Nothing from Phase 0 onward has been deployed.** Production is also still
  missing `bulk-select.js` and `keys.js`.
- A gate move reaches enforcement at the next nightly retrain; the zero-RCU
  path to making it immediate is written up at the end of the Phase 1b plan.
- The full suite has not run in one process since Phase 1d: the machine ran
  low on memory and the run was stopped. It was run here in two halves,
  880 + 91, with the same total coverage gate applied to neither half.
