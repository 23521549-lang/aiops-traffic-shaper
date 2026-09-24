# Phase 2 — the operations console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the operations console answer its two questions honestly. Stop
three gauges reporting a constant, give every tenant number a denominator and
a seven-day trend so the one lever can be aimed, fold the Agents page into the
tenant it belongs to, and wire the last two of spec 2.5's four audit records.

**Architecture:** This surface inherits the type scale and the colour tokens
and nothing else. There is no sigma axis here, and that is a technical fact
rather than a style choice: z is normalised against each tenant's own training
distribution, so no cross-tenant sigma exists to draw. It reuses the
measurement component with a different quantity, and its quantity is the quota
gauge.

**Tech Stack:** FastAPI, Jinja2, htmx 2.0.4 (vendored), DynamoDB single-table.

**Spec:** `docs/ui-rebuild/05-spec-thiet-ke.md` §6, §2.5, §1.4

## Global Constraints

- No em dash may appear in any rendered UI string.
- CSP is `default-src 'self'; script-src 'self'; style-src 'self'`. No inline
  `style=` attribute, no inline `<script>`.
- Every mutation is a body-less POST with its values in the query string, sent
  by htmx so the CSRF token is inherited from the console wrapper's
  `hx-headers`.
- Total CSS across `services/backend/ui/static/*.css` stays under 80,000
  bytes. It is at 78,283 at the start of this phase, so this phase has about
  1,700 bytes. Reuse `c-figure`, `meter`, `c-table` and `c-state`; this
  surface is explicitly not allowed a visual language of its own.
- No new table and no new GSI. `BatchGetItem` caps at 100 keys per call, and
  anything that would exceed it must be bounded rather than silently
  truncated.
- Principle 1.4: a gauge that shows a constant is a lie with a number on it.
  Either measure it or take it off the screen.
- **Never a bare count.** `presenters.usage_share` exists for this and the
  tenant table ignores it.
- Coverage gate: 80%. Run `ruff check` as a separate command from pytest.
- The machine has been running low on memory. Run the suite in parts
  (`services/backend/tests/` excluding `test_training_poisoning.py`, then that
  file plus `services/agent/tests/`) rather than in one process, and say so
  when reporting results.

---

## Task 1: Three gauges that have never measured anything

`record_invocation(resource)` is called from exactly one place
(`main.py:172`) and never passes `estimated_gb_seconds`, so the parameter
takes its default of `0.0` and the overview has displayed **GB-seconds: 0.00**
since the day it shipped.

Worse, and not in the spec: `dynamodb_consumed_rcu` and
`dynamodb_consumed_wcu` are on `UsageReport`, are served by
`GET /admin/v1/usage`, and **nothing writes them anywhere in the codebase**.
Two more permanent zeroes, one of them in a public API response.

The spec's rule is "measured for real, or taken off the screen". GB-seconds is
measurable with nothing new: the middleware already wraps every request, so
duration is free, and Lambda publishes its memory size in the environment.
Consumed capacity is not: it would need `ReturnConsumedCapacity` on every call
and an aggregation write per request, on the write budget this gauge exists to
protect. So it goes.

**Files:**
- Modify: `services/backend/main.py` (the middleware measures)
- Modify: `services/backend/core/usage.py` (`UsageReport`, `record_invocation`)
- Modify: `services/backend/ui/templates/admin_overview.html`
- Test: `services/backend/tests/test_usage_measured.py` (create)

**Interfaces:**
- Consumes: `UsageCountersTable.add_invocation(date, estimated_gb_seconds)`,
  which already writes the field with an `ADD`.
- Produces: `usage.lambda_memory_gb() -> float`, and `record_invocation`
  called with a real figure. `UsageReport` loses `dynamodb_consumed_rcu` and
  `dynamodb_consumed_wcu`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_usage_measured.py`:

```python
"""A gauge that shows a constant is a lie with a number on it.

`record_invocation` has one caller and it has never passed
`estimated_gb_seconds`, so the operations overview has displayed 0.00 since
the day it shipped. `dynamodb_consumed_rcu` and `dynamodb_consumed_wcu` are
worse: they are served by GET /admin/v1/usage and nothing in this codebase
writes them at all.

Principle 1.4 gives two options and no third. GB-seconds is measurable with
nothing new - the middleware already wraps every request and Lambda publishes
its memory size - so it is measured. Consumed capacity would need
ReturnConsumedCapacity on every call and an aggregation write per request, on
the very write budget the gauge exists to protect, so it goes.
"""


def test_a_request_records_the_time_it_actually_took(dynamo_resource):
    """Not a constant, and not a guess: the middleware times the call it is
    already wrapping."""
    ...


def test_the_figure_scales_with_the_configured_memory(monkeypatch):
    """GB-seconds is memory times duration. A function configured at 512MB
    bills half of what one at 1024MB bills for the same wall time."""
    ...


def test_an_unset_memory_size_falls_back_to_the_deployed_value(monkeypatch):
    """Locally there is no Lambda environment. The fallback must be the size
    this product is actually deployed at, so a developer reading the figure
    is reading the same units as production."""
    ...


def test_the_recorded_total_accumulates_across_requests(dynamo_resource):
    ...


def test_consumed_capacity_is_gone_from_the_report(dynamo_resource):
    """Removed rather than left at zero. It was in a public API response,
    where a permanent zero is a lie to a machine as well as a person."""
    from services.backend.core.usage import get_usage_report

    report = get_usage_report(dynamo_resource, None)

    assert not hasattr(report, "dynamodb_consumed_rcu")


def test_consumed_capacity_is_gone_from_the_screen():
    from pathlib import Path

    tpl = Path("services/backend/ui/templates/admin_overview.html")

    assert "rcu" not in tpl.read_text(encoding="utf-8").lower()


def test_the_overview_shows_a_gb_seconds_figure_that_can_move(client, dynamo_resource):
    ...
```

Fill every `...` with a real body before running. A test whose body is an
ellipsis passes and asserts nothing.

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_usage_measured.py -q`

- [ ] **Step 3: Measure it**

In `services/backend/core/usage.py`:

```python
# What this function is configured at. Lambda publishes it; locally there is
# no Lambda, and the fallback is the size the product is actually deployed at
# so a figure read on a laptop is in the same units as one read in production.
_DEFAULT_MEMORY_MB = 512


def lambda_memory_gb() -> float:
    import os

    try:
        mb = int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE",
                                _DEFAULT_MEMORY_MB))
    except ValueError:
        mb = _DEFAULT_MEMORY_MB
    return mb / 1024
```

and in the middleware at `main.py`, time the call that is already being
wrapped and pass `lambda_memory_gb() * elapsed_seconds`.

Then delete `dynamodb_consumed_rcu` and `dynamodb_consumed_wcu` from
`UsageReport` and from `get_usage_report`.

- [ ] **Step 4: Run the tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add services/backend/main.py services/backend/core/usage.py \
        services/backend/ui/templates/admin_overview.html \
        services/backend/tests/test_usage_measured.py
git commit -m "fix(ops): three gauges that showed a constant now measure or are gone"
```

---

## Task 2: A denominator on every number, and a week of them

`_tenants_table.html` renders `<td class="d">{{ row.used }}</td>` - a bare
count, on the one screen whose own presenter module defines `usage_share`
specifically to forbid that, with the docstring "Never a bare number".

And the operator's only lever is Suspend, which needs aiming. A single day's
figure cannot distinguish a tenant that has always been busy from one that
started flooding an hour ago, and those call for opposite actions.

Seven days per tenant is seven keys, and the per-tenant counters are keyed
`YYYY-MM-DD#tenant#<id>` on the same table. `UsageCountersTable.get_many` was
built in Phase 0 for exactly this and has never had a caller.

**Files:**
- Modify: `services/backend/core/usage.py` (`tenant_requests_over`)
- Modify: `services/backend/ui/control_platform.py` (`_tenants_ctx`)
- Modify: `services/backend/ui/templates/_tenants_table.html`
- Modify: `services/backend/ui/static/app.css`
- Test: `services/backend/tests/test_tenant_usage_trend.py` (create)

**Interfaces:**
- Consumes: `UsageCountersTable.get_many(keys) -> dict[str, dict]`,
  `usage_share(used, ceiling) -> dict`, `tenant_daily_quota() -> int`.
- Produces: `usage.tenant_requests_over(resource, tenant_ids, days=7) ->
  dict[str, list[int]]`, oldest day first, zero-filled.

**The bound that must not be forgotten:** `BatchGetItem` takes at most 100
keys. Seven days times N tenants crosses that at 15 tenants. The function must
page or cap, and a cap must be visible on screen rather than silent.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_tenant_usage_trend.py`:

```python
"""The one lever this console has, given something to aim at.

Suspend is the operator's only action. A single day's count cannot tell a
tenant that has always been busy apart from one that started flooding an
hour ago, and those two call for opposite decisions.

The counters have been written on every accepted batch since the per-tenant
quota shipped, and `UsageCountersTable.get_many` was built in Phase 0 to read
several at once. Neither has ever been read for this.
"""


def test_a_week_of_counts_comes_back_oldest_first(dynamo_resource):
    """A chart is read left to right and a trend line has a direction."""
    ...


def test_a_day_with_no_row_is_a_zero_and_not_a_gap(dynamo_resource):
    """Unlike telemetry, a missing day here genuinely means no requests: the
    counter is written on every accepted batch, so its absence is a
    measurement."""
    ...


def test_several_tenants_come_back_in_one_call(dynamo_resource, monkeypatch):
    """One GetItem per tenant per day is 70 round trips for ten tenants on a
    page load. get_many exists for this and had no caller."""
    ...


def test_the_batch_is_bounded_rather_than_silently_truncated(dynamo_resource):
    """BatchGetItem takes 100 keys. Seven days times fifteen tenants crosses
    it, and DynamoDB answers a too-large batch with an error, not with the
    first hundred - but a caller that pages without a limit turns one page
    load into an unbounded read instead."""
    ...


def test_the_tenant_row_states_the_denominator(client, dynamo_resource):
    """presenters.usage_share exists for this and its docstring is "Never a
    bare number". The screen that ignored it is the screen that module was
    written for."""
    page = client.get("/admin/ui/tenants").text

    assert "of 8,333" in page or "/ 8333" in page


def test_the_row_shows_the_trend_not_only_today(client, dynamo_resource):
    ...


def test_a_tenant_flooding_today_reads_differently_from_one_always_busy(client, dynamo_resource):
    """The distinction the lever needs. Both have the same count today."""
    ...
```

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Read the week in one call**

```python
def tenant_requests_over(resource, tenant_ids: list[str], days: int = 7,
                         today: str | None = None) -> dict[str, list[int]]:
    """Seven days for several tenants, in as few round trips as possible.

    One GetItem per tenant per day is seventy calls for ten tenants on a page
    load. BatchGetItem bills the same capacity for the same rows and costs one
    call, which is what `get_many` was built for in Phase 0 and never used
    for.

    Oldest day first: a trend has a direction and a chart is read left to
    right. A day with no row is a real zero, not a gap - the counter is
    written on every accepted batch, so its absence is a measurement.
    """
```

with an explicit cap: `tenant_ids` beyond what `days` allows inside 100 keys
is refused with a `ValueError`, and the caller shows the first N with a line
saying how many are not shown. A silent truncation on a billing screen is the
same class of defect as a gauge showing a constant.

- [ ] **Step 4: Put the denominator and the trend on the row**

`_tenants_ctx` builds `usage_share(used, tenant_daily_quota())` per row and a
seven-element list. The table cell shows the share, and a sparkline drawn as
seven `<rect>` in one small inline SVG - no new component, no new CSS beyond
the bar fill, and bucketed heights so nothing is computed into a style
attribute.

- [ ] **Step 5: Run everything, lint, commit**

```bash
git commit -m "feat(ops): every tenant number has a denominator and a week behind it"
```

---

## Task 3: The Agents page is a filter, not a job

Spec §6: "The Agents page on the operations side is a filter over a list, not
a job. Fold it into the tenant page."

`/admin/ui/agents` exists, has its own nav item and its own template, and
answers a question that is a column on the tenant page. Meanwhile
`_tenant_detail` already queries that tenant's agents and the detail pane
already lists them.

Two jobs, and only two, is the constraint this surface is under. A third nav
item is a third job.

**Files:**
- Modify: `services/backend/ui/control_platform.py` (drop `agents_page`)
- Delete: `services/backend/ui/templates/admin_agents.html`
- Modify: `services/backend/ui/templates/admin_overview.html` (the quiet-agent
  figure links to the tenants list, not to a page of its own)
- Modify: the admin shell's nav
- Test: `services/backend/tests/test_ops_two_jobs.py` (create)

- [ ] **Step 1: Write the failing test**

```python
def test_the_operations_console_offers_exactly_two_jobs(client):
    """Am I about to be billed, and which tenant caused it. A third nav item
    is a third job, and this surface is explicitly not allowed one."""
    ...


def test_the_agents_page_is_gone(client):
    assert client.get("/admin/ui/agents").status_code == 404


def test_a_quiet_agent_is_still_findable(client, dynamo_resource):
    """Deleting the page must not delete the answer. It was a filter over a
    list, and the list is the tenant table."""
    ...


def test_the_tenant_pane_still_names_every_agent(client, dynamo_resource):
    ...


def test_nothing_still_links_to_the_removed_page():
    ...
```

- [ ] **Step 2 through 4:** run it red, remove the route, the template and the
nav item, point the overview figure at `/admin/ui/tenants`, run it green.

- [ ] **Step 5: Commit**

```bash
git commit -m "refactor(ops): the agents page was a filter, and it belongs on the tenant"
```

---

## Task 4: The last two audit records

Spec §2.5 names four actions that earn an append-only row. Phase 0 built the
mechanism, Phase 1c wired the whitelist pair, and these two have never been
wired at all: **tenant suspend and reactivate**, and **agent key mint and
revoke**.

Both are exactly the test the spec sets for the list: a reasonable person
could later dispute either with money, blame or security attached. Suspending
a tenant stops their protection and revokes every key they hold.

**Files:**
- Modify: `services/backend/ui/control_platform.py`
- Modify: `services/backend/api/routes/admin.py`
- Test: `services/backend/tests/test_ops_audit.py` (create)

**Interfaces:**
- Consumes: `TenantHistoryTable.record_setting(tenant_id, actor, what, old,
  new, now, because=None)` and `_try_history` from
  `services/backend/api/routes/agent.py`.
- The row is written **on the affected tenant's partition**, not on an
  operator partition: the question it answers is "what happened to this
  tenant", and it has to be readable by the same `query_settings` the console
  already uses.

- [ ] **Step 1: Write the failing test**

```python
def test_suspending_a_tenant_is_recorded(client, dynamo_resource):
    """It stops their protection and revokes every key they hold. If one
    action in this product earns a row, it is this one."""
    ...


def test_reactivating_is_recorded_too(client, dynamo_resource):
    """A log that says a tenant was suspended and never says it came back is
    the more dangerous half of the pair."""
    ...


def test_minting_an_agent_key_is_recorded(client, dynamo_resource):
    ...


def test_revoking_an_agent_key_is_recorded(client, dynamo_resource):
    ...


def test_the_row_names_the_operator_from_the_verified_token(client, dynamo_resource):
    """Never from a request field. That is what keeps the row fixed-size and
    what makes it worth anything in a dispute."""
    ...


def test_the_row_lands_on_the_affected_tenants_partition(client, dynamo_resource):
    """The question it answers is "what happened to this tenant", and it has
    to come back from the same query_settings the console already uses."""
    ...


def test_a_throttled_audit_write_does_not_stop_the_suspension(client, dynamo_resource):
    """Same rule as every other reporting write: the ledger is worth having
    and it is not worth losing the suspension to a throttled table."""
    ...


def test_all_four_audited_actions_are_now_wired():
    """Spec 2.5 names four. Three phases shipped with the mechanism built and
    some of them unwired, and nothing said which."""
    ...
```

- [ ] **Step 2 through 4:** run red, wire both pairs through `_try_history`,
run green.

- [ ] **Step 5: Run everything in parts, lint, commit**

```bash
PYTHONPATH=. python -m pytest services/backend/tests/ -q \
  --ignore=services/backend/tests/test_training_poisoning.py
PYTHONPATH=. python -m pytest services/backend/tests/test_training_poisoning.py \
  services/agent/tests/ -q
ruff check services/backend services/agent
git commit -m "feat(ops): the last two of the four actions that earn an audit row"
```

---

## Self-review

**Spec coverage.** §6 has five requirements. No sigma axis here: already true
and asserted in Task 3's tests. Two jobs and only two: Task 3. A denominator
on the tenant request count plus a seven-day trend: Task 2.
`estimated_gb_seconds` measured or removed: Task 1, which also removes two
more constant-zero fields the spec did not know about. The Agents page folded
into the tenant page: Task 3. §2.5's remaining two records: Task 4.

**Type consistency.** `tenant_requests_over` (Task 2) returns
`dict[str, list[int]]` and is consumed only by `_tenants_ctx`.
`lambda_memory_gb` (Task 1) is consumed only by the middleware.
`UsageReport` loses two fields in Task 1 and is a response model on
`GET /admin/v1/usage`, so that is a contract change - stated here rather than
discovered.

**Known risks, stated rather than designed around.** Removing two fields from
`UsageReport` narrows a public API response; they have never held anything but
zero, so no consumer can be relying on a value, but a consumer reading the key
will now get a `KeyError`. Task 2's BatchGetItem is bounded at 100 keys and
the bound is visible on screen. Task 3 deletes a URL; the product is
invite-only and the page has no external inbound links, and the tests pin that
the answer it gave is still reachable.
