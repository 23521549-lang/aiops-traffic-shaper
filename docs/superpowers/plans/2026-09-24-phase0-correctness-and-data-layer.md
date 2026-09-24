# Phase 0 — Correctness and Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the one correctness defect in the product, and make the data layer able to answer the questions the rebuilt UI will ask — with no UI work at all.

**Architecture:** Nine independent changes to the backend, the agent and two static files. Every one of them adds bytes to writes that already happen, or removes reads that already happen; the only new capacity ask in the whole phase is +1 RCU on one table. Nothing here renders a pixel.

**Tech Stack:** Python 3.12, FastAPI, boto3/DynamoDB, pytest + moto, vanilla JS (no build step).

**Spec:** `docs/ui-rebuild/05-spec-thiet-ke.md` — sections 2.1 through 2.9. Read it before Task 1; the plan argues from it.

## Global Constraints

- **Test runner.** `scripts/run_tests.sh` does not work on the maintainer's Windows machine. Run pytest and ruff as **separate** commands and read the `N failed, M passed` line, never the exit code — chaining them with `;` lets ruff's exit code mask a pytest failure.
  - `PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q --cov=services/backend --cov=services/agent --cov-fail-under=80`
  - `ruff check services/backend services/agent`
- **TDD is the repo convention.** Every test is named after the defect it prevents and is written failing first. Test docstrings explain *why the defect mattered*, not what the code does.
- **Commits:** exactly one `-m`. Stage explicit paths — **never `git add -A` at the repo root**, there are 2.5GB of user ZIPs sitting there. No AI attribution in any commit message.
- **Capacity budget:** 14 RCU / 20 WCU provisioned of 25/25 account-wide. Headroom 11 RCU, 5 WCU. Nothing in this phase may add a write operation; only bytes to existing writes.
- **No em dash in anything rendered to a user.**
- **`_try_history` wrapping:** any new write on the ingest path is reporting, not product. It goes through `_try_history` so a throttled write cannot stop an agent receiving the decisions it needs to enforce.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `services/agent/enforcer/__init__.py` | `DecisionStore` gains `reconcile()` | 1 |
| `services/agent/runner.py` | calls `reconcile()` only on an accepted batch | 1 |
| `services/backend/schemas/telemetry.py` | `TelemetryResponse` gains `active_ips` | 1 |
| `services/backend/api/routes/agent.py` | serves `active_ips`; counts near-threshold bins; passes features to history | 1, 4, 5 |
| `services/backend/ui/static/keys.js` | lazy `#palette` lookup | 2 |
| `services/backend/ui/templates/base.html` | htmx config | 3 |
| `docs/adr/007-portal-redesign.md` | amend the history-config rationale | 3 |
| `services/backend/core/tables.py` | `record_decision` takes features; `record_traffic` takes bins; `ModelsTable.get_metadata`; `UsageCountersTable.get_many` | 4, 5, 6, 9 |
| `services/backend/schemas/history.py` | `MitigationEpisode` gains features + stats version | 4 |
| `services/backend/ml/registry.py` | `ScoreStats` gains per-tenant thresholds; `save_model` preserves them | 7 |
| `services/backend/ml/model.py` | `classify`/`z_score` read thresholds from stats | 7 |
| `services/backend/api/cognito_auth.py` | `dashboard_auth` returns claims | 8 |
| `terraform/dynamodb.tf` | `UsageCounters` 1 → 2 RCU | 9 |

---

## Task 1: The agent lifts a block the backend has stopped serving

This is the product's one correctness defect. The console says "Any block on it has been lifted" and the rule stays in the customer's nginx for up to an hour.

**Files:**
- Modify: `services/backend/schemas/telemetry.py`
- Modify: `services/backend/api/routes/agent.py`
- Modify: `services/agent/enforcer/__init__.py`
- Modify: `services/agent/runner.py`
- Test: `services/agent/tests/test_reconcile.py` (create)
- Test: `services/backend/tests/test_active_ips.py` (create)

**Interfaces:**
- Consumes: `MitigationStateTable.query_active(tenant_id)` (exists).
- Produces: `TelemetryResponse.active_ips: list[str]`; `DecisionStore.reconcile(active_ips: list[str], adapters: list) -> list[str]` returning the IPs it lifted.

- [ ] **Step 1: Write the failing agent test**

Create `services/agent/tests/test_reconcile.py`:

```python
"""A block the backend has stopped serving must come out of nginx.

`add_whitelist` deletes the MitigationState row, so the backend stops
ISSUING the block. The agent never noticed: `apply()` only adds and
`sweep_expired()` only removes on local TTL. The deny rule stood for up to an
hour while the console said "Any block on it has been lifted".

The set to reconcile against is the FULL active set, not the decisions in the
telemetry response. Those cover only IPs with traffic in that batch, and a
blocked IP stops sending traffic - that is what being blocked means. An
earlier draft of the spec got this wrong and would have unblocked every
attacker seconds after blocking them.
"""
import time

from services.agent.enforcer import DecisionStore
from services.agent.tests.test_runner import FakeAdapter


def _blocked(ip, seconds=3600):
    return {"ip": ip, "tier": 2, "expires_at": int(time.time()) + seconds}


def test_an_ip_the_backend_no_longer_serves_is_unblocked():
    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7")], [adapter])

    lifted = store.reconcile(["198.51.100.1"], [adapter])

    assert lifted == ["203.0.113.7"]
    assert adapter.unblocked == ["203.0.113.7"]
    assert store.active_ips() == []


def test_an_ip_still_served_is_left_alone():
    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7")], [adapter])

    assert store.reconcile(["203.0.113.7"], [adapter]) == []
    assert adapter.unblocked == []


def test_an_empty_served_set_lifts_everything():
    """The backend accepted the batch and is issuing nothing. That is a real
    state - every timer expired - and it must reach the enforcers."""
    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7"), _blocked("198.51.100.1")], [adapter])

    assert sorted(store.reconcile([], [adapter])) == ["198.51.100.1", "203.0.113.7"]


def test_an_adapter_that_throws_does_not_strand_the_others():
    failing, ok = FakeAdapter(), FakeAdapter()
    failing.unblock = lambda ip: (_ for _ in ()).throw(OSError("iptables gone"))
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7")], [ok])

    store.reconcile([], [failing, ok])

    assert ok.unblocked == ["203.0.113.7"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/agent/tests/test_reconcile.py -q`
Expected: FAIL, `AttributeError: 'DecisionStore' object has no attribute 'reconcile'`

- [ ] **Step 3: Implement `reconcile`**

In `services/agent/enforcer/__init__.py`, add to `DecisionStore` directly after `sweep_expired`:

```python
    def reconcile(self, served_ips: list[str], adapters: list[EnforcementAdapter]) -> list[str]:
        """Drop anything the backend has stopped serving.

        `sweep_expired` only handles the timer running out. Nothing handled
        the other way a block ends: a human allowing the IP, which deletes the
        MitigationState row so the backend stops issuing it. The rule then sat
        in the customer's nginx until its local TTL, while the console said it
        had been lifted.

        `served_ips` must be the FULL active set for the tenant, never the
        decisions from one telemetry response - those cover only IPs with
        traffic in that batch, and a blocked IP stops sending traffic.
        """
        served = set(served_ips)
        gone = [ip for ip in self._active if ip not in served]
        for ip in gone:
            for adapter in adapters:
                try:
                    adapter.unblock(ip)
                except Exception as e:
                    logger.error("%s failed to unblock ip=%s: %s", adapter.name, ip, e)
            del self._active[ip]
        return gone
```

- [ ] **Step 4: Run it and watch it pass**

Run: `PYTHONPATH=. python -m pytest services/agent/tests/test_reconcile.py -q`
Expected: 4 passed

- [ ] **Step 5: Write the failing test for the safety rule**

Append to `services/agent/tests/test_reconcile.py`:

```python
def test_a_failed_batch_never_lifts_anything():
    """The single most dangerous line in this feature. A backend outage must
    not read as "nothing is enforced any more" - that would strip every
    protection at exactly the moment an attack is causing the load."""
    from services.agent.http_client import BackendError
    from services.agent.runner import run_loop
    from services.agent.tests.test_runner import _collector, _record

    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7")], [adapter])

    collector = _collector([BackendError("503")], [], batch_size=2)
    run_loop([_record(), _record()], collector, store, [adapter])

    assert adapter.unblocked == []
    assert store.active_ips() == ["203.0.113.7"]


def test_an_accepted_batch_does_reconcile():
    from services.agent.runner import run_loop
    from services.agent.tests.test_runner import _collector, _record

    adapter = FakeAdapter()
    store = DecisionStore()
    store.apply([_blocked("203.0.113.7")], [adapter])

    collector = _collector([{"decisions": [], "active_ips": []}], [], batch_size=2)
    run_loop([_record(), _record()], collector, store, [adapter])

    assert adapter.unblocked == ["203.0.113.7"]
```

- [ ] **Step 6: Run it and watch the second one fail**

Run: `PYTHONPATH=. python -m pytest services/agent/tests/test_reconcile.py -q`
Expected: `test_a_failed_batch_never_lifts_anything` PASSES already (the guard exists); `test_an_accepted_batch_does_reconcile` FAILS, `assert [] == ['203.0.113.7']`

- [ ] **Step 7: Call reconcile from the runner**

In `services/agent/runner.py`, replace the body of `_apply`:

```python
def _apply(store, adapters, result) -> None:
    """Enforce whatever came back, and stop enforcing whatever did not.

    A response with no decisions key at all is the normal answer for a tenant
    whose model has not been trained yet, which is every tenant on their first
    day.

    `result` is None when the POST failed, and that is load-bearing: a backend
    outage must never reconcile, because an empty served set would strip every
    block at exactly the moment an attack is causing the load.
    """
    if not result:
        return
    try:
        store.apply(result.get("decisions") or [], adapters)
        if "active_ips" in result:
            store.reconcile(result["active_ips"] or [], adapters)
    except Exception:
        logger.exception("applying decisions failed")
```

Note the `"active_ips" in result` guard: an older backend that does not send the key must not be read as "nothing is served".

- [ ] **Step 8: Run the agent suite**

Run: `PYTHONPATH=. python -m pytest services/agent/tests/ -q`
Expected: all pass

- [ ] **Step 9: Write the failing backend test**

Create `services/backend/tests/test_active_ips.py`:

```python
"""The telemetry response carries the full active set, so the agent can
reconcile.

`decisions[]` is built from `touched_ips` - only addresses with traffic in
that batch. A blocked IP stops sending traffic, so reconciling against
`decisions[]` would unblock every attacker seconds after blocking them. The
full set costs one Query per batch and about 800 bytes for fifty addresses;
the alternative, polling /agent/v1/decisions every 60s, costs 69% of a
tenant's daily request share for four agents.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantsTable, create_all_tables,
)
from services.backend.api.dependencies import hash_api_key
from services.backend.main import app


@pytest.fixture
def client(dynamo_resource):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at="2026-08-21T00:00:00Z")
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01",
        registered_at="2026-09-01T00:00:00+00:00",
        last_seen_at="2026-09-01T00:00:00+00:00",
        agent_version="1.4.0", api_key_hash=hash_api_key("secret"), status="active")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app, base_url="https://testserver")


def _post(client, logs=()):
    return client.post("/agent/v1/telemetry", json={"logs": list(logs)},
                       headers={"X-Agent-Key": "t-1.secret"})


def test_the_response_lists_every_active_mitigation(client, dynamo_resource):
    """Including ones with no traffic in this batch - which is every blocked
    IP, because blocked IPs stop sending traffic."""
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="203.0.113.7", tier=2, score=-0.6, z=-6.0,
        reason="behavioral_anomaly", expires_at=0)

    body = _post(client).json()

    assert body["active_ips"] == ["203.0.113.7"]


def test_it_never_lists_another_tenants_addresses(client, dynamo_resource):
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-2", ip="198.51.100.9", tier=2, score=-0.6, z=-6.0,
        reason="behavioral_anomaly", expires_at=0)

    assert _post(client).json()["active_ips"] == []


def test_an_empty_set_is_an_empty_list_not_a_missing_key(client):
    """The agent distinguishes "the backend told me nothing is served" from
    "this backend is too old to tell me". Only the second may be ignored."""
    body = _post(client).json()

    assert "active_ips" in body
    assert body["active_ips"] == []
```

- [ ] **Step 10: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_active_ips.py -q`
Expected: FAIL, `KeyError: 'active_ips'`

- [ ] **Step 11: Add the field and populate it**

In `services/backend/schemas/telemetry.py`, extend `TelemetryResponse`:

```python
class TelemetryResponse(BaseModel):
    received: int
    processed_ips: int
    decisions: list[MitigationState] = []
    # Every address currently in force for this tenant, not only the ones
    # this batch touched. The agent reconciles its local enforcement against
    # this: a blocked IP stops sending traffic, so `decisions` alone would
    # tell the agent to release every attacker. A list of strings rather than
    # full MitigationState objects - fifty of those is ~10KB every five
    # seconds, which is ~172MB/day of egress for a set membership test.
    active_ips: list[str] = []
```

In `services/backend/api/routes/agent.py`, replace the `return TelemetryResponse(...)` at the end of the telemetry route:

```python
    # One Query, ~1 RCU for a typical twenty rows. At the account-wide ingest
    # ceiling of ~0.39 batches/second that is ~0.39 RCU sustained, inside the
    # 11 RCU of headroom. It is what lets the agent stop enforcing a block a
    # human has lifted, instead of holding it for up to an hour.
    active = MitigationStateTable(resource).query_active(tenant_id)

    return TelemetryResponse(
        received=len(batch.logs), processed_ips=len(touched_ips), decisions=decisions,
        active_ips=[item["ip"] for item in active],
    )
```

- [ ] **Step 12: Run it and watch it pass**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_active_ips.py -q`
Expected: 3 passed

- [ ] **Step 13: Run both suites and lint**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q`
Then: `ruff check services/backend services/agent`
Expected: all pass, all checks passed

- [ ] **Step 14: Commit**

```bash
git add services/agent/enforcer/__init__.py services/agent/runner.py \
        services/agent/tests/test_reconcile.py \
        services/backend/schemas/telemetry.py \
        services/backend/api/routes/agent.py \
        services/backend/tests/test_active_ips.py
git commit -m "fix(agent): a block a human lifted stayed in nginx for up to an hour"
```

---

## Task 2: `keys.js` looks the palette up lazily

**Files:**
- Modify: `services/backend/ui/static/keys.js:17`
- Test: `services/backend/tests/test_shipped_scripts.py`

**Interfaces:**
- Produces: nothing. This is a prerequisite for Task 3 and must land first.

- [ ] **Step 1: Write the failing test**

Append to `services/backend/tests/test_shipped_scripts.py`:

```python
def test_the_palette_is_looked_up_lazily():
    """`keys.js` captured `#palette` once at load. Enabling htmx history
    means a restore swap replaces the children of <body>, so that reference
    goes stale and Ctrl+K silently stops opening the palette after a back
    press - the exact defect class test_keyboard.py exists to prevent.

    `ui-status.js` and `bulk-select.js` survive the same swap because both
    delegate from document.body, and the body node itself is not replaced.
    """
    text = (_STATIC / "keys.js").read_text(encoding="utf-8")

    assert "var palette = document.getElementById" not in text, (
        "a reference captured at load time does not survive a history swap")
    assert text.count('getElementById("palette")') >= 1
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_shipped_scripts.py -q`
Expected: FAIL on `test_the_palette_is_looked_up_lazily`

- [ ] **Step 3: Make the lookup lazy**

In `services/backend/ui/static/keys.js`, replace line 17:

```js
  /* Looked up on use, never captured.
   *
   * With htmx history enabled, a restore swap replaces the children of
   * <body>, so a reference taken at load time points at a detached node and
   * Ctrl+K silently stops working after a back press. */
  function palette() { return document.getElementById("palette"); }
```

Then replace every bare `palette` with `palette()` inside the functions that use it, and hoist it to a local where a function uses it more than once. For example `openPalette` becomes:

```js
  function openPalette() {
    var dialog = palette();
    if (!dialog || dialog.open) return;
    var input = dialog.querySelector("[data-palette-input]");
    if (input) { input.value = ""; }
    filter("");
    dialog.showModal();
    if (input) input.focus();
  }
```

Do the same in `items()`, `filter()`, and the listener-attaching block. The listeners are attached once at load against the dialog that exists then; after a restore swap they are gone with it, so also move the palette listener attachment into a function and call it from an `htmx:afterSwap` handler, mirroring what `bulk-select.js` already does:

```js
  document.body.addEventListener("htmx:afterSwap", wirePalette);
```

- [ ] **Step 4: Run the script tests**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_shipped_scripts.py services/backend/tests/test_keyboard.py -q`
Expected: all pass (`node --check` covers the syntax)

- [ ] **Step 5: Commit**

```bash
git add services/backend/ui/static/keys.js services/backend/tests/test_shipped_scripts.py
git commit -m "fix(console): the palette reference did not survive a history swap"
```

---

## Task 3: htmx keeps URLs and stops caching markup

**Files:**
- Modify: `services/backend/ui/templates/base.html:33` and its comment block
- Modify: `docs/adr/007-portal-redesign.md`
- Test: `services/backend/tests/test_htmx_config.py` (create)

**Interfaces:**
- Consumes: Task 2 must be complete.
- Produces: `hx-push-url` becomes usable by Phase 1.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_htmx_config.py`:

```python
"""htmx keeps working URLs without persisting tenant markup.

`historyEnabled:false` was chosen to stop htmx writing page HTML into
localStorage, where it would be tenant-scoped markup on a possibly shared
machine. That reason is sound and the flag was too broad: it also disabled
hx-push-url, so a swap left the address bar stale and a pasted link
reproduced a different view than the one on screen.

`historyCacheSize:0` addresses the stated concern directly. In the vendored
file the save function short-circuits and returns before any write, and
removes a cache left by an earlier build:

    historyCacheSize<=0){localStorage.removeItem("htmx-history-cache");return
"""
import json
import re
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[1] / "ui" / "templates"
_STATIC = Path(__file__).resolve().parents[1] / "ui" / "static"


def _config() -> dict:
    html = (_TEMPLATES / "base.html").read_text(encoding="utf-8")
    raw = re.search(r"""name="htmx-config" content='([^']+)'""", html).group(1)
    return json.loads(raw)


def test_urls_are_pushed_so_a_pasted_link_shows_what_was_on_screen():
    assert _config()["historyEnabled"] is True


def test_no_page_markup_is_persisted_to_localstorage():
    """The security property the original flag was chosen for."""
    assert _config()["historyCacheSize"] == 0


def test_the_vendored_htmx_actually_honours_a_zero_cache_size():
    """Read the bytes, not the docs. The config is only meaningful if the
    shipped build short-circuits on it."""
    js = (_STATIC / "htmx.min.js").read_text(encoding="utf-8", errors="replace")

    assert 'historyCacheSize<=0){localStorage.removeItem("htmx-history-cache");return' in js


def test_the_other_lockdowns_are_untouched():
    """These three are independent of the history flags and each removes a
    capability the CSP would otherwise have to allow."""
    config = _config()

    assert config["allowEval"] is False
    assert config["allowScriptTags"] is False
    assert config["includeIndicatorStyles"] is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_htmx_config.py -q`
Expected: 2 failed (`historyEnabled` is False, `historyCacheSize` absent), 2 passed

- [ ] **Step 3: Change the config and its comment**

In `services/backend/ui/templates/base.html`, replace the `historyEnabled` paragraph of the comment block with:

```
    historyEnabled      stays ON so hx-push-url works and a pasted link
                        reproduces the view that was on screen.
    historyCacheSize    0. This is the flag that carries the security
                        property: htmx would otherwise write page HTML into
                        localStorage, which in a multi-tenant portal is
                        tenant-scoped markup persisted on a possibly shared
                        machine. At 0 the save function returns before any
                        write and removes a cache an earlier build left.
                        Verified by reading the vendored bytes; confirmed in
                        a browser at rollout, per ADR-007.
```

and line 33 with:

```html
<meta name="htmx-config" content='{"allowEval":false,"allowScriptTags":false,"includeIndicatorStyles":false,"historyEnabled":true,"historyCacheSize":0}'>
```

- [ ] **Step 4: Run it and watch it pass**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_htmx_config.py -q`
Expected: 4 passed

- [ ] **Step 5: Amend the ADR**

In `docs/adr/007-portal-redesign.md`, find the passage justifying `historyEnabled:false` and append:

```markdown
**Amended 2026-09-24.** The rationale above is the localStorage concern, and
`historyCacheSize: 0` addresses it directly: the vendored build's save
function returns before any write and removes a cache left by an earlier
build. `historyEnabled: false` also disabled `hx-push-url`, which cost the
console the property it deliberately bought with `?ip=` — that a view can be
pasted into a ticket. The config is now
`historyEnabled: true, historyCacheSize: 0`, which obtains the stated
security property without that cost. This is not a reversal of the decision;
it is the same decision with a more precise flag.

The rule in this ADR that config overrides are confirmed in a browser at
rollout rather than assumed still applies, and applies to this change.
```

- [ ] **Step 6: Commit**

```bash
git add services/backend/ui/templates/base.html services/backend/tests/test_htmx_config.py docs/adr/007-portal-redesign.md
git commit -m "fix(console): buy the localStorage guarantee without giving up working URLs"
```

- [ ] **Step 7: Browser confirmation — REQUIRES THE MAINTAINER**

ADR-007 requires config overrides to be confirmed in a real browser, not inferred from the minified source. The local server must be running, and **it may only be started when the maintainer asks** (it has been reaped twice for memory pressure).

Confirm, with the console open:
1. `localStorage.getItem("htmx-history-cache")` is `null` after navigating several console pages.
2. An `hx-push-url` navigation changes the address bar.
3. Back, then Ctrl+K: the palette opens. (This is what Task 2 protects.)

Record the result in the commit message of whatever ships next, or in the ADR.

---

## Task 4: The evidence for a decision survives the hour

**Files:**
- Modify: `services/backend/schemas/history.py`
- Modify: `services/backend/core/tables.py` — `TenantHistoryTable.record_decision`
- Modify: `services/backend/api/routes/agent.py` — the `_try_history` call
- Test: `services/backend/tests/test_episode_evidence.py` (create)

**Interfaces:**
- Consumes: `ScoreStats` from `services.backend.ml.registry`.
- Produces: `MitigationEpisode.last_features: list[float]` and `MitigationEpisode.stats_version: str | None`; `record_decision(..., features: list[float] | None = None, stats_version: str | None = None)`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_episode_evidence.py`:

```python
"""Why a source was blocked has to outlive the block.

The seven-feature vector lives only on the MitigationState row, whose TTL is
five minutes or one hour. MitigationEpisode keeps thirty days and had no
features at all, so the product's one unmatched capability - decomposing a
decision against this tenant's own baseline - could be shown only while the
block was still in force, and never at the moment a customer actually asks,
which is afterwards.

The fix is bytes on a write that already happens: ~184 B to ~284 B, still one
1 KB write unit, still 1 WCU, zero additional operations.

`stats_version` travels with them because `z` is frozen at decision time
while the baseline columns are read from the currently loaded model. One
nightly retrain between deciding and viewing makes "your normal" and the
sigma beside it describe two different models, silently, on the one screen
that is the whole differentiator.
"""
import pytest

from services.backend.core.tables import TenantHistoryTable, create_all_tables


@pytest.fixture
def history(dynamo_resource):
    create_all_tables(dynamo_resource)
    return TenantHistoryTable(dynamo_resource)


HOUR = 1758700800
VECTOR = [8.4, 0.71, 90.0, 0.003, 0.02, 0.9, 0.94]


def test_the_feature_vector_is_stored_with_the_episode(history):
    history.record_decision("t-1", "203.0.113.7", hour_start=HOUR, tier=2,
                            now=HOUR + 5, score=-0.31, z=-8.2,
                            features=VECTOR, stats_version="v-2026-09-24")

    episode = history.query_episodes("t-1", HOUR, HOUR + 3600)[0]

    assert [float(f) for f in episode["last_features"]] == VECTOR


def test_the_baseline_it_was_measured_against_is_named(history):
    history.record_decision("t-1", "203.0.113.7", hour_start=HOUR, tier=2,
                            now=HOUR + 5, score=-0.31, z=-8.2,
                            features=VECTOR, stats_version="v-2026-09-24")

    episode = history.query_episodes("t-1", HOUR, HOUR + 3600)[0]

    assert episode["stats_version"] == "v-2026-09-24"


def test_a_decision_with_no_features_still_records(history):
    """A tenant with no model scores nothing and has no vector. The episode
    must still exist - the decision was taken."""
    history.record_decision("t-1", "203.0.113.7", hour_start=HOUR, tier=1,
                            now=HOUR + 5, score=-0.12, z=None)

    episode = history.query_episodes("t-1", HOUR, HOUR + 3600)[0]

    assert episode.get("last_features") in (None, [])


def test_the_episode_row_stays_under_one_write_unit(history):
    """The rule for this row: a fixed-size numeric record, capped at 1 KB, no
    variable-length text ever. `record_decision` fires once per IP per hour,
    so a 3,000-IP hour at 2 WCU would be 6,000 WCU against a 2-WCU table."""
    history.record_decision("t-1", "203.0.113.7", hour_start=HOUR, tier=2,
                            now=HOUR + 5, score=-0.31, z=-8.2,
                            features=VECTOR, stats_version="v-2026-09-24")

    episode = history.query_episodes("t-1", HOUR, HOUR + 3600)[0]
    size = sum(len(str(k)) + len(str(v)) for k, v in episode.items())

    assert size < 1024, f"episode row is {size} B; the cap is 1024"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_episode_evidence.py -q`
Expected: FAIL, `record_decision() got an unexpected keyword argument 'features'`

- [ ] **Step 3: Widen `record_decision`**

In `services/backend/core/tables.py`, change the signature and the expression:

```python
    def record_decision(self, tenant_id: str, ip: str, hour_start: int, tier: int,
                        now: int, score: float, z: float | None,
                        features: list[float] | None = None,
                        stats_version: str | None = None) -> None:
```

Inside, after the existing `tier = int(tier)` line, build the optional clause:

```python
        # Bytes on a write that already happens: ~184 B to ~284 B, still one
        # 1 KB unit, still 1 WCU, zero extra operations. The version id
        # travels with the vector because `z` is frozen at decision time
        # while the baseline is read live - without it, one nightly retrain
        # makes the two halves of the explanation describe different models.
        evidence = ""
        extra: dict = {}
        if features:
            evidence += ", last_features = :f"
            extra[":f"] = [float(x) for x in features]
        if stats_version:
            evidence += ", stats_version = :sv"
            extra[":sv"] = stats_version
```

Then insert `{evidence}` into the `SET` clause, immediately before `first_ts`:

```python
            update_expression=(
                "ADD tier1_count :t1, tier2_count :t2 "
                "SET last_ts = :now, ip = :ip, hour_start = :h, "
                "last_score = :s, last_z = :z" + evidence + ", "
                "first_ts = if_not_exists(first_ts, :now), #ttl = :ttl"
            ),
```

and merge `extra` into `expr_values`:

```python
            expr_values={
                ":t1": 1 if tier == 1 else 0,
                ":t2": 1 if tier >= 2 else 0,
                ":now": int(now), ":ip": ip, ":h": int(hour_start),
                ":s": score, ":z": z,
                ":ttl": int(hour_start) + self.RETENTION_SECONDS,
                **extra,
            },
```

- [ ] **Step 4: Run it and watch it pass**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_episode_evidence.py -q`
Expected: 4 passed

- [ ] **Step 5: Add the schema fields**

In `services/backend/schemas/history.py`, add to `MitigationEpisode` after `last_z`:

```python
    # The vector that justified the decision, and the id of the statistics it
    # was measured against. Optional because episodes written before this
    # existed have neither, and a page must render them rather than 500.
    last_features: list[float] = Field(default_factory=list)
    stats_version: str | None = None
```

- [ ] **Step 6: Pass them from the decision loop**

In `services/backend/api/routes/agent.py`, extend the `_try_history` call:

```python
            _try_history(history.record_decision, tenant_id, v.remote_addr,
                         hour_start=hour_start, tier=int(tier), now=int(now),
                         score=score, z=state.z,
                         features=state.features,
                         stats_version=getattr(mgr.stats, "version", None))
```

- [ ] **Step 7: Give `ScoreStats` the version it is being asked for**

`getattr(mgr.stats, "version", None)` above would return None forever:
`ScoreStats` has no such field and no other task produces one, so
`stats_version` would be dead on arrival. The `Models` item already carries
`version` — it simply never travelled into the stats.

In `services/backend/ml/registry.py`, add to `ScoreStats`:

```python
    # Which saved model these figures came from. Written onto every episode
    # beside the feature vector, because `z` is frozen at decision time while
    # the baseline is read live - without this the two halves of the
    # explanation can describe different models and nothing would say so.
    version: str | None = None
```

and in `load_model_and_stats`, inside the `ScoreStats(...)` construction:

```python
            version=item.get("version"),
```

- [ ] **Step 8: Prove the version actually reaches the episode**

Append to `services/backend/tests/test_episode_evidence.py`:

```python
def test_the_stats_carry_a_version_for_the_episode_to_record(dynamo_resource):
    """Without this the field is written as None forever, and the guarantee
    it exists for - that "your normal" and the sigma beside it describe the
    same model - is silently absent."""
    import numpy as np

    from services.backend.core.tables import create_all_tables
    from services.backend.ml.registry import load_model_and_stats
    from services.backend.ml.training import train_and_save

    create_all_tables(dynamo_resource)
    rng = np.random.default_rng(11)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(dynamo_resource, "t-1", rows, stage="production")

    _, stats = load_model_and_stats(dynamo_resource, "t-1")

    assert stats.version
```

- [ ] **Step 9: Run the full backend suite**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ -q`
Expected: all pass

- [ ] **Step 10: Commit**

```bash
git add services/backend/core/tables.py services/backend/schemas/history.py \
        services/backend/api/routes/agent.py services/backend/ml/registry.py \
        services/backend/tests/test_episode_evidence.py
git commit -m "feat(history): the reason for a block outlives the block"
```

---

## Task 5: Thirteen near-threshold bins, for free

**Files:**
- Modify: `services/backend/core/tables.py` — `TenantHistoryTable.record_traffic`
- Modify: `services/backend/api/routes/agent.py` — the decision loop and the `record_traffic` call
- Test: `services/backend/tests/test_near_threshold_bins.py` (create)

**Interfaces:**
- Produces: `TenantHistoryTable.NEAR_BINS: tuple[float, ...]`, `TenantHistoryTable.bin_name(sigma: float) -> str | None`, and `record_traffic(..., bins: dict[str, int] | None = None)`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_near_threshold_bins.py`:

```python
"""The gate can only be previewed in one direction without these.

`agent.py` does `if tier == AnomalyTier.NORMAL: continue`, so a source below
4 sigma leaves no trace anywhere. Raising a gate is backtestable from
MitigationEpisode.last_z; lowering it is not, because the sources it would
newly catch were never written down - and lowering is the direction an
operator fears, because it is the direction that starts blocking real
customers.

Thirteen counters folded into the ADD that record_traffic already issues:
zero extra operations, zero extra WCU, ~104 B on an item with 905 B spare.
"""
import pytest

from services.backend.core.tables import TenantHistoryTable, create_all_tables

HOUR = 1758700800


@pytest.fixture
def history(dynamo_resource):
    create_all_tables(dynamo_resource)
    return TenantHistoryTable(dynamo_resource)


def test_the_bins_cover_three_to_six_sigma_in_quarters():
    """Twelve bins of 0.25 plus one overflow. The floor is 3.0 on product
    grounds: ADR-006 measured 0.82% false positives at 3.5 sigma and the rate
    climbs steeply below it, so the control must not offer a gate it cannot
    honestly recommend. The ceiling matches SIGMA_CEILING in charts.py."""
    assert len(TenantHistoryTable.NEAR_BINS) == 13
    assert TenantHistoryTable.NEAR_BINS[0] == 3.0
    assert TenantHistoryTable.NEAR_BINS[-1] == 6.0


@pytest.mark.parametrize("sigma,expected", [
    (2.9, None),
    (3.0, "n300"),
    (3.2, "n300"),
    (3.25, "n325"),
    (4.0, "n400"),
    (5.99, "n575"),
    (6.0, "n600"),
    (9.9, "n600"),
])
def test_a_magnitude_lands_in_the_bin_below_it(sigma, expected):
    assert TenantHistoryTable.bin_name(sigma) == expected


def test_a_negative_magnitude_is_rejected_rather_than_binned():
    """z is negative by convention and the caller passes its magnitude. A
    sign slip would silently file every source in the lowest bin."""
    with pytest.raises(ValueError):
        TenantHistoryTable.bin_name(-4.0)


def test_only_the_bins_that_were_hit_are_written(history):
    """ADD creates a missing numeric attribute, so a bin never hit costs zero
    bytes and zero expression length."""
    history.record_traffic("t-1", HOUR, requests=10, bins={"n400": 2})

    row = history.query_series("t-1", HOUR, HOUR + 3600, fill=False)[0]

    assert int(row["n400"]) == 2
    assert "n325" not in row


def test_counts_accumulate_across_batches(history):
    history.record_traffic("t-1", HOUR, requests=10, bins={"n400": 2})
    history.record_traffic("t-1", HOUR, requests=10, bins={"n400": 3, "n325": 1})

    row = history.query_series("t-1", HOUR, HOUR + 3600, fill=False)[0]

    assert int(row["n400"]) == 5
    assert int(row["n325"]) == 1


def test_the_existing_counters_are_untouched(history):
    """"What the gate did" is a different fact from "what it would have
    done"; both are kept."""
    history.record_traffic("t-1", HOUR, requests=10, tier1=1, tier2=2,
                           bins={"n400": 1})

    row = history.query_series("t-1", HOUR, HOUR + 3600, fill=False)[0]

    assert int(row["tier1_decisions"]) == 1
    assert int(row["tier2_decisions"]) == 2
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_near_threshold_bins.py -q`
Expected: FAIL, `AttributeError: type object 'TenantHistoryTable' has no attribute 'NEAR_BINS'`

- [ ] **Step 3: Add the bin scheme**

In `services/backend/core/tables.py`, inside `TenantHistoryTable`, above `record_traffic`:

```python
    # Twelve bins of 0.25 sigma from 3.0, plus one overflow at 6.0 matching
    # SIGMA_CEILING in ui/charts.py so the data and the chart agree. The
    # floor is 3.0 on product grounds, not byte grounds: ADR-006 measured
    # 0.82% false positives at 3.5 sigma and the rate climbs steeply below
    # it, so the gate control must not offer a setting it cannot honestly
    # recommend.
    #
    # Names are `n` plus sigma x 100: 4 B of name, 4 B of value, 8 B a bin.
    # The agg# item is ~119 B with 905 B spare, so thirteen bins cost 104 B
    # and the row stays inside one 1 KB write unit.
    NEAR_BINS: tuple[float, ...] = tuple(3.0 + 0.25 * i for i in range(13))

    @classmethod
    def bin_name(cls, magnitude: float) -> str | None:
        """Which bin a |z| falls in, or None below the floor.

        Takes a MAGNITUDE. z is negative by convention, and a sign slip would
        file every source in the lowest bin without complaining, so a
        negative argument raises rather than being helpfully absolved.
        """
        if magnitude < 0:
            raise ValueError("bin_name takes |z|, not z")
        if magnitude < cls.NEAR_BINS[0]:
            return None
        edge = min(cls.NEAR_BINS, key=lambda b: (magnitude < b, -b))
        return "n%d" % round(edge * 100)
```

- [ ] **Step 4: Run the bin tests**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_near_threshold_bins.py -q -k "bins or magnitude or negative"`
Expected: the three naming tests pass, the write tests still fail

- [ ] **Step 5: Fold the bins into `record_traffic`**

Replace `record_traffic` in `services/backend/core/tables.py`:

```python
    def record_traffic(self, tenant_id: str, hour_start: int, requests: int,
                       tier1: int = 0, tier2: int = 0,
                       bins: dict[str, int] | None = None) -> None:
        """One atomic ADD per telemetry batch - a constant, independent of
        how many IPs the batch touched. Fixed-size scalars only: anything
        that grows with request volume is the trap add_aggregate was written
        to avoid.

        `bins` rides in the same ADD, so the near-threshold histogram that
        makes the gate previewable in both directions costs zero extra
        operations and zero extra WCU. Only bins the batch actually hit are
        emitted; ADD creates a missing numeric attribute, so a bin never hit
        costs nothing. The read side must zero-fill, because an absent bin
        comes back absent rather than 0 - the same thing query_series(fill)
        already does for empty hours.
        """
        adds = ["requests :r", "batches :b",
                "tier1_decisions :t1", "tier2_decisions :t2"]
        values = {":r": int(requests), ":b": 1,
                  ":t1": int(tier1), ":t2": int(tier2),
                  ":h": int(hour_start),
                  ":ttl": int(hour_start) + self.RETENTION_SECONDS}
        for i, (name, count) in enumerate(sorted((bins or {}).items())):
            adds.append("%s :nb%d" % (name, i))
            values[":nb%d" % i] = int(count)

        self.update(
            key={"tenant_id": tenant_id, "sk": self.series_sk(hour_start)},
            update_expression=("ADD " + ", ".join(adds) +
                               " SET hour_start = :h, #ttl = :ttl"),
            expr_names={"#ttl": "ttl"},
            expr_values=values,
        )
```

- [ ] **Step 6: Run the bin tests again**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_near_threshold_bins.py -q`
Expected: 11 passed

- [ ] **Step 7: Count near-threshold sources in the decision loop**

In `services/backend/api/routes/agent.py`, inside the scoring loop, replace the `continue` for normal traffic so the magnitude is counted first:

```python
        for v, score in mgr.score_vectors([vector]):
            tier = classify(score, mgr.stats)
            if tier == AnomalyTier.NORMAL:
                # Counted, not stored. A source below the gate is never acted
                # on individually, so no IP is retained - only the shape. This
                # is what lets the gate preview LOWERING as well as raising,
                # and it is why the strip may state a count and must never
                # offer a "show me them" link.
                z = z_score(score, mgr.stats)
                if z is not None:
                    name = TenantHistoryTable.bin_name(abs(z))
                    if name:
                        near_bins[name] = near_bins.get(name, 0) + 1
                continue
```

Declare `near_bins: dict[str, int] = {}` next to `decisions: list[MitigationState] = []`, and pass it:

```python
    _try_history(history.record_traffic, tenant_id, hour_start=hour_start,
                 requests=len(batch.logs),
                 tier1=sum(1 for d in decisions if d.tier == 1),
                 tier2=sum(1 for d in decisions if d.tier >= 2),
                 bins=near_bins)
```

- [ ] **Step 8: Run the whole backend suite and lint**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ -q`
Then: `ruff check services/backend services/agent`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add services/backend/core/tables.py services/backend/api/routes/agent.py \
        services/backend/tests/test_near_threshold_bins.py
git commit -m "feat(history): record the shape below the gate so it can be previewed downward"
```

---

## Task 6: Reading the model metadata stops costing 30 RCU

**Files:**
- Modify: `services/backend/core/tables.py` — `ModelsTable`
- Modify: `services/backend/api/routes/dashboard.py` — `model_status`
- Test: `services/backend/tests/test_model_metadata_read.py` (create)

**Interfaces:**
- Produces: `ModelsTable.get_metadata(tenant_id: str, stage_version: str = "production") -> dict | None`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_model_metadata_read.py`:

```python
"""Printing a version string should not cost thirty read units.

`model_status` called ModelsTable.get() with no ProjectionExpression, which
pulls the ~238 KB model blob to read `version` and `trained_at` - about 30
RCU against a table provisioned at 2. It is the worst read-to-value ratio in
the product, and it is on a page a customer is invited to open.

The projection pattern already exists in registry.model_exists.
"""
import pytest

from services.backend.core.tables import ModelsTable, create_all_tables


@pytest.fixture
def models(dynamo_resource):
    create_all_tables(dynamo_resource)
    table = ModelsTable(dynamo_resource)
    table.put(tenant_id="t-1", stage_version="production", version="v7",
              trained_at="2026-09-23T02:00:00Z", training_samples=4096,
              contamination=0.02, score_mean=-0.05, score_std=0.011,
              model_blob="x" * 200_000)
    return table


def test_the_metadata_comes_back_without_the_blob(models):
    item = models.get_metadata("t-1")

    assert item["version"] == "v7"
    assert item["training_samples"] == 4096
    assert "model_blob" not in item


def test_the_statistics_needed_to_draw_a_baseline_are_included(models):
    """The model page is meant to show the tenant the shape of their own
    normal, which needs the per-feature figures, not only the version."""
    item = models.get_metadata("t-1")

    assert "score_mean" in item
    assert "score_std" in item


def test_a_missing_model_is_none_not_an_exception(models):
    assert models.get_metadata("t-2") is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_model_metadata_read.py -q`
Expected: FAIL, `AttributeError: 'ModelsTable' object has no attribute 'get_metadata'`

- [ ] **Step 3: Add the projected read**

In `services/backend/core/tables.py`, inside `ModelsTable`:

```python
    # Everything the console shows, and nothing that weighs 238 KB.
    _METADATA_FIELDS = (
        "tenant_id", "stage_version", "version", "trained_at",
        "training_samples", "contamination", "score_mean", "score_std",
        "feature_means", "feature_stds", "features", "stage",
        "tier1_z", "tier2_z",
    )

    def get_metadata(self, tenant_id: str, stage_version: str = "production") -> dict | None:
        """The model item without its blob: ~30 RCU down to ~0.5.

        A bare get() pulls the serialised IsolationForest - about 238 KB - to
        print a version string, on a table provisioned at 2 RCU. Same pattern
        as registry.model_exists.

        `stage` and `stats` are reserved words in DynamoDB, so every field is
        aliased rather than guessing which ones need it.
        """
        names = {"#f%d" % i: f for i, f in enumerate(self._METADATA_FIELDS)}
        resp = self._table.get_item(
            Key={"tenant_id": tenant_id, "stage_version": stage_version},
            ProjectionExpression=", ".join(names),
            ExpressionAttributeNames=names,
        )
        return resp.get("Item")
```

- [ ] **Step 4: Run it and watch it pass**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_model_metadata_read.py -q`
Expected: 3 passed

- [ ] **Step 5: Use it from the status route**

In `services/backend/api/routes/dashboard.py`, in `model_status`, replace the read:

```python
    # Projected: the blob is 238 KB and this route prints a version string.
    item = ModelsTable(resource).get_metadata(tenant_id=tenant_id,
                                              stage_version="production")
```

- [ ] **Step 6: Run the backend suite and lint**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ -q`
Then: `ruff check services/backend services/agent`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add services/backend/core/tables.py services/backend/api/routes/dashboard.py \
        services/backend/tests/test_model_metadata_read.py
git commit -m "perf(model): stop reading a 238KB blob to print a version string"
```

---

## Task 7: Thresholds become a per-tenant value

**Files:**
- Modify: `services/backend/ml/registry.py` — `ScoreStats`, `load_model_and_stats`, `save_model`
- Modify: `services/backend/ml/model.py` — `classify`, `z_score` callers
- Modify: `services/backend/ml/training.py` — carry the tenant's value into the saved model
- Test: `services/backend/tests/test_per_tenant_thresholds.py` (create)

**Interfaces:**
- Consumes: `TenantsTable` (exists).
- Produces: `ScoreStats.tier1_z: float` and `ScoreStats.tier2_z: float`, defaulting to the module constants.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_per_tenant_thresholds.py`:

```python
"""The console explained a decision it did not let anyone change.

TIER1_Z and TIER2_Z were module constants, so the product could say "this
source is 6.4 standard deviations outside your normal" and offer no way to
say "for me, act at 4.5". That is why two competent redesigns still read as
dashboards: every verb was in a corner. Making the threshold a per-tenant
value is the structural change the rest of the interface is built on.

The value lives on Tenants, which assert_tenant_active already reads on every
authenticated agent request, and the nightly retrain copies it onto the
production Models item so classify() finds it in the stats already cached for
scoring. Zero reads, zero RCU, zero WCU on the hot path.

Storing it ONLY on Models fails: save_model rewrites that item every night
and would silently revert whatever the operator set.
"""
import pytest

from services.backend.ml.model import TIER1_Z, TIER2_Z, AnomalyTier, classify
from services.backend.ml.registry import ScoreStats


def _stats(**over):
    base = dict(mean=-0.05, std=0.01)
    base.update(over)
    return ScoreStats(**base)


def test_the_default_is_the_shipped_threshold():
    """A tenant that has never set one behaves exactly as before."""
    stats = _stats()

    assert stats.tier1_z == TIER1_Z
    assert stats.tier2_z == TIER2_Z


def test_a_tenant_can_be_harder_to_trip():
    """z = -4.5 tiers as NORMAL for this tenant and RATE_LIMIT by default."""
    score = -0.05 + (-4.5 * 0.01)

    assert classify(score, _stats()) is AnomalyTier.RATE_LIMIT
    assert classify(score, _stats(tier1_z=-5.0, tier2_z=-6.0)) is AnomalyTier.NORMAL


def test_a_tenant_can_be_easier_to_trip():
    score = -0.05 + (-3.5 * 0.01)

    assert classify(score, _stats()) is AnomalyTier.NORMAL
    assert classify(score, _stats(tier1_z=-3.0, tier2_z=-4.0)) is AnomalyTier.RATE_LIMIT


def test_the_block_tier_uses_the_tenants_own_second_threshold():
    score = -0.05 + (-5.5 * 0.01)

    assert classify(score, _stats()) is AnomalyTier.HARD_BLOCK
    assert classify(score, _stats(tier1_z=-5.0, tier2_z=-6.0)) is AnomalyTier.RATE_LIMIT


def test_degenerate_spread_still_falls_back_to_absolute_thresholds():
    """score_std of zero means every training bucket scored identically.
    Dividing by it on the request path would be a crash, so classify falls
    back - and a per-tenant z cannot change that, because there is no z."""
    assert classify(-0.5, _stats(std=0.0, tier1_z=-3.0)) is AnomalyTier.HARD_BLOCK


def test_saving_a_model_does_not_revert_the_operators_threshold(dynamo_resource):
    """The nightly retrain rewrites the production Models item. If the
    threshold lived only there, every night would silently undo the operator."""
    import numpy as np

    from services.backend.core.tables import TenantsTable, create_all_tables
    from services.backend.ml.registry import load_model_and_stats
    from services.backend.ml.training import train_and_save

    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z",
                                      tier1_z=-4.5, tier2_z=-5.5)
    rng = np.random.default_rng(3)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]

    train_and_save(dynamo_resource, "t-1", rows, stage="production")
    _, stats = load_model_and_stats(dynamo_resource, "t-1")

    assert stats.tier1_z == -4.5
    assert stats.tier2_z == -5.5
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_per_tenant_thresholds.py -q`
Expected: FAIL, `TypeError: ScoreStats.__init__() got an unexpected keyword argument 'tier1_z'`

- [ ] **Step 3: Widen `ScoreStats`**

In `services/backend/ml/registry.py`:

```python
@dataclass
class ScoreStats:
    """Where this model's own training scores sat. Anomaly tiers are measured
    in standard deviations from that mean, because decision_function has no
    fixed meaning across models - see docs/adr/006-score-calibration.md."""
    mean: float
    std: float
    feature_means: list[float] = field(default_factory=list)
    feature_stds: list[float] = field(default_factory=list)
    # The tenant's own gate, defaulting to the shipped one. It rides with the
    # stats because those are already in ModelManager._cache when classify()
    # runs, so a per-tenant threshold costs nothing on the scoring path.
    tier1_z: float = TIER1_Z_DEFAULT
    tier2_z: float = TIER2_Z_DEFAULT
```

with, at the top of the file:

```python
# Duplicated here rather than imported from ml.model, which imports this
# module. The test below asserts the two stay equal.
TIER1_Z_DEFAULT = -4.0
TIER2_Z_DEFAULT = -5.0
```

- [ ] **Step 4: Make `classify` use them**

In `services/backend/ml/model.py`, replace the two comparisons:

```python
    z = z_score(score, stats)
    if z is None:
        return classify_score(score)
    tier2 = stats.tier2_z if stats else TIER2_Z
    tier1 = stats.tier1_z if stats else TIER1_Z
    if z < tier2:
        return AnomalyTier.HARD_BLOCK
    if z < tier1:
        return AnomalyTier.RATE_LIMIT
    return AnomalyTier.NORMAL
```

- [ ] **Step 5: Add the guard test for the duplicated constants**

Append to `services/backend/tests/test_per_tenant_thresholds.py`:

```python
def test_the_duplicated_defaults_have_not_drifted():
    """registry cannot import ml.model - ml.model imports registry - so the
    defaults are written twice. This is the only thing keeping them equal."""
    from services.backend.ml import registry

    assert registry.TIER1_Z_DEFAULT == TIER1_Z
    assert registry.TIER2_Z_DEFAULT == TIER2_Z
```

- [ ] **Step 6: Persist and load the value**

In `services/backend/ml/registry.py`, in `load_model_and_stats`, add to the `ScoreStats(...)` construction:

```python
            tier1_z=float(item["tier1_z"]) if "tier1_z" in item else TIER1_Z_DEFAULT,
            tier2_z=float(item["tier2_z"]) if "tier2_z" in item else TIER2_Z_DEFAULT,
```

In `services/backend/ml/training.py`, read the tenant's value before saving and pass it into the saved item:

```python
    # Copied onto the model item each night so classify() finds it in the
    # stats it already has cached. The value of record lives on Tenants; a
    # model-only home would be silently reverted by this very function.
    tenant = TenantsTable(resource).get(tenant_id=tenant_id) or {}
    tier1_z = float(tenant.get("tier1_z", TIER1_Z_DEFAULT))
    tier2_z = float(tenant.get("tier2_z", TIER2_Z_DEFAULT))
```

and include `tier1_z=tier1_z, tier2_z=tier2_z` in the `save_model` call.

- [ ] **Step 7: Run the tests**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_per_tenant_thresholds.py services/backend/tests/test_score_calibration.py -q`
Expected: all pass

- [ ] **Step 8: Run the whole suite and lint**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q --cov=services/backend --cov=services/agent --cov-fail-under=80`
Then: `ruff check services/backend services/agent`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add services/backend/ml/registry.py services/backend/ml/model.py \
        services/backend/ml/training.py \
        services/backend/tests/test_per_tenant_thresholds.py
git commit -m "feat(ml): the gate becomes a value the tenant owns, not a module constant"
```

---

## Task 8: Mutations record who did them

**Files:**
- Modify: `services/backend/api/cognito_auth.py` — `dashboard_auth`
- Modify: `services/backend/core/tables.py` — `TenantHistoryTable`
- Modify: `services/backend/api/routes/dashboard.py` — `add_whitelist`
- Test: `services/backend/tests/test_audit_records.py` (create)

**Interfaces:**
- Produces: `dashboard_auth` still returns `str`; a new `dashboard_claims` dependency returns the full claims dict. `TenantHistoryTable.record_setting(tenant_id, actor, what, old, new, now)`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_audit_records.py`:

```python
"""Four actions in this product can be disputed later, with money, blame or
security attached. Those four get an append-only record. Nothing else does,
and nothing gets an undo - all four already have an inverse action.

The architect's hard-no list said "no audit trail" and the BA's success
criteria demanded one for every mutating action. Both narrowed: this is four
append-only rows, not an audit subsystem, and it fits at 1 WCU per change on
a table that already exists.
"""
import pytest

from services.backend.core.tables import TenantHistoryTable, create_all_tables

NOW = 1758700805


@pytest.fixture
def history(dynamo_resource):
    create_all_tables(dynamo_resource)
    return TenantHistoryTable(dynamo_resource)


def test_a_threshold_change_records_who_when_and_both_values(history):
    """The value that changes enforcement for all future traffic on a live
    site. Non-negotiable."""
    history.record_setting("t-1", actor="ops@example.com", what="tier1_z",
                           old=-4.0, new=-4.5, now=NOW)

    rows = history.query_settings("t-1", NOW - 60, NOW + 60)

    assert len(rows) == 1
    assert rows[0]["actor"] == "ops@example.com"
    assert rows[0]["what"] == "tier1_z"
    assert float(rows[0]["old"]) == -4.0
    assert float(rows[0]["new"]) == -4.5


def test_records_are_append_only_and_do_not_overwrite(history):
    history.record_setting("t-1", actor="a@x", what="tier1_z", old=-4.0,
                           new=-4.5, now=NOW)
    history.record_setting("t-1", actor="b@x", what="tier1_z", old=-4.5,
                           new=-5.0, now=NOW + 1)

    assert len(history.query_settings("t-1", NOW - 60, NOW + 60)) == 2


def test_one_tenants_records_are_not_another_tenants(history):
    history.record_setting("t-2", actor="a@x", what="tier1_z", old=-4.0,
                           new=-4.5, now=NOW)

    assert history.query_settings("t-1", NOW - 60, NOW + 60) == []


def test_the_record_row_carries_no_free_text_from_the_request(history):
    """The episode row rule applies here too: fixed-size, capped. `actor`
    comes from a verified JWT claim, never from a form field."""
    history.record_setting("t-1", actor="ops@example.com", what="tier1_z",
                           old=-4.0, new=-4.5, now=NOW)

    row = history.query_settings("t-1", NOW - 60, NOW + 60)[0]
    size = sum(len(str(k)) + len(str(v)) for k, v in row.items())

    assert size < 1024
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_audit_records.py -q`
Expected: FAIL, `AttributeError: ... has no attribute 'record_setting'`

- [ ] **Step 3: Add the fourth sort-key prefix**

In `services/backend/core/tables.py`, inside `TenantHistoryTable`, next to the other `*_sk` helpers:

```python
    @staticmethod
    def setting_sk(now: int) -> str:
        """A fourth prefix on the same single-table design. No new table, no
        provisioning floor, no GSI, and it reads through the existing
        query/between path."""
        return "set#%010d" % int(now)

    def record_setting(self, tenant_id: str, actor: str, what: str,
                       old, new, now: int) -> None:
        """One PutItem per change, ~200 B, 1 WCU, 30-day TTL.

        Append-only and never updated: the point of the row is that it
        records a thing that happened. `actor` comes from a verified JWT
        claim, never from a request field, which is what keeps this row
        fixed-size.
        """
        self.put(
            tenant_id=tenant_id,
            sk=self.setting_sk(now),
            actor=actor,
            what=what,
            old=old,
            new=new,
            at=int(now),
            ttl=int(now) + self.RETENTION_SECONDS,
        )

    def query_settings(self, tenant_id: str, since: int, until: int) -> list[dict]:
        from boto3.dynamodb.conditions import Key
        return self._query_all_pages(
            KeyConditionExpression=Key("tenant_id").eq(tenant_id)
            & Key("sk").between(self.setting_sk(since), self.setting_sk(until)),
        )
```

- [ ] **Step 4: Run it and watch it pass**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_audit_records.py -q`
Expected: 4 passed

- [ ] **Step 5: Write the failing test for the actor**

Append to `services/backend/tests/test_audit_records.py`:

```python
def test_the_whitelist_records_who_added_the_entry(dynamo_resource, cognito_test_keys):
    """`added_by` is in docs/schema.md and has never been written. In a
    multi-user tenant, "who let this IP in, and why" had no answer."""
    from fastapi.testclient import TestClient

    from services.backend.api.cognito_auth import get_jwks
    from services.backend.core.dynamo import get_dynamo_resource
    from services.backend.core.tables import WhitelistTable
    from services.backend.main import app
    from services.backend.tests.conftest import sign_test_token

    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    client.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"custom:tenant_id": "t-1", "email": "ops@example.com"})})

    client.post("/dashboard/ui/whitelist/203.0.113.4",
                headers={"X-CSRF-Token": client.cookies["csrf_token"]})

    entry = WhitelistTable(dynamo_resource).get(tenant_id="t-1", ip="203.0.113.4")
    assert entry["added_by"] == "ops@example.com"
```

- [ ] **Step 6: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_audit_records.py -q -k added`
Expected: FAIL, `KeyError: 'added_by'`

- [ ] **Step 7: Return the claims and write the actor**

In `services/backend/api/cognito_auth.py`, add beside `dashboard_auth`:

```python
def dashboard_claims(authorization: str | None = Header(default=None),
                     x_id_token: str | None = Header(default=None),
                     id_token: str | None = Cookie(default=None),
                     jwks: dict = Depends(get_jwks)) -> dict:
    """The verified claims, for the four actions that need an actor.

    `dashboard_auth` keeps returning just the tenant id: nearly every route
    wants only that, and widening its return type would touch every caller
    for the benefit of four.
    """
    claims = _decode_and_verify(_extract_token(authorization, id_token, x_id_token), jwks)
    if not claims.get("custom:tenant_id"):
        raise HTTPException(status_code=401, detail="Token missing tenant_id claim")
    return claims


def actor_of(claims: dict) -> str:
    """Who to name in a record. Email when the pool carries one, subject id
    otherwise; never a request field."""
    return claims.get("email") or claims.get("sub") or "unknown"
```

In `services/backend/api/routes/dashboard.py`, add the dependency to `add_whitelist` and write the field:

```python
def add_whitelist(body: WhitelistRequest, tenant_id: str = Depends(dashboard_auth),
                  claims: dict = Depends(dashboard_claims),
                  resource=Depends(get_dynamo_resource)):
```

and include `added_by=actor_of(claims)` in the `WhitelistTable(...).put(...)` call.

- [ ] **Step 8: Run the suite and lint**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/ -q`
Then: `ruff check services/backend services/agent`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add services/backend/api/cognito_auth.py services/backend/core/tables.py \
        services/backend/api/routes/dashboard.py \
        services/backend/tests/test_audit_records.py
git commit -m "feat(audit): four actions that can be disputed now record who did them"
```

---

## Task 9: "Am I protected right now" becomes one cheap read

**Files:**
- Modify: `services/backend/core/tables.py` — `UsageCountersTable`
- Modify: `services/backend/core/usage.py`
- Modify: `terraform/dynamodb.tf`
- Test: `services/backend/tests/test_protection_status.py` (create)

**Interfaces:**
- Produces: `UsageCountersTable.get_many(keys: list[str]) -> dict[str, dict]`; `usage.protection_status(resource, tenant_id) -> dict` with keys `tenant_used`, `tenant_ceiling`, `global_used`, `global_ceiling`, `throttled`, `throttled_reason`.

- [ ] **Step 1: Write the failing test**

Create `services/backend/tests/test_protection_status.py`:

```python
"""A tenant inside its own quota can still be unprotected.

`enforce_usage_ceiling` refuses telemetry PLATFORM-WIDE at the global
ceiling, so a tenant well inside its 25% share stops being measured because
someone else filled the day. Today the agent sees a 429 and the console says
nothing, so the screen and the agent disagree about whether the customer is
protected.

Two keys on one table, so one BatchGetItem: ~1 RCU, one round trip.
"""
import pytest

from services.backend.core.tables import UsageCountersTable, create_all_tables
from services.backend.core.usage import (
    _DAILY_REQUEST_CEILING, _today, protection_status, record_tenant_ingest,
    tenant_daily_quota,
)


@pytest.fixture
def resource(dynamo_resource):
    create_all_tables(dynamo_resource)
    return dynamo_resource


def _fill_the_day(resource, requests: int) -> None:
    """Seed the global counter directly, the way test_usage_throttle.py does.
    `add_invocation` takes no count and calling it a million times is not a
    test, it is a wait."""
    UsageCountersTable(resource).put(
        date=_today(), total_requests=requests, estimated_gb_seconds=0.0)


def test_a_quiet_day_is_not_throttled(resource):
    status = protection_status(resource, "t-1")

    assert status["throttled"] is False
    assert status["throttled_reason"] is None


def test_the_tenants_own_share_is_reported_with_its_denominator(resource):
    """Never a bare number - a count with no denominator cannot tell anyone
    whether to worry."""
    record_tenant_ingest(resource, "t-1", count=100)

    status = protection_status(resource, "t-1")

    assert status["tenant_used"] == 100
    assert status["tenant_ceiling"] == tenant_daily_quota()


def test_the_global_ceiling_throttles_a_tenant_inside_its_own_share(resource):
    """The case nobody has designed for, and the one that makes a customer
    unprotected through no fault of their own."""
    _fill_the_day(resource, int(_DAILY_REQUEST_CEILING))

    status = protection_status(resource, "t-1")

    assert status["throttled"] is True
    assert status["throttled_reason"] == "global"


def test_a_tenant_over_its_own_share_is_named_as_such(resource):
    record_tenant_ingest(resource, "t-1", count=tenant_daily_quota() + 1)

    status = protection_status(resource, "t-1")

    assert status["throttled"] is True
    assert status["throttled_reason"] == "tenant"


def test_both_keys_are_fetched_in_one_round_trip(resource, monkeypatch):
    """One BatchGetItem, not two GetItems. The table is provisioned at 2 RCU
    and this runs on every page."""
    calls = []
    original = UsageCountersTable.get_many

    def counting(self, keys):
        calls.append(tuple(keys))
        return original(self, keys)

    monkeypatch.setattr(UsageCountersTable, "get_many", counting)
    protection_status(resource, "t-1")

    assert len(calls) == 1
    assert len(calls[0]) == 2
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_protection_status.py -q`
Expected: FAIL, `ImportError: cannot import name 'protection_status'`

- [ ] **Step 3: Add the batch read**

In `services/backend/core/tables.py`, inside `UsageCountersTable`:

```python
    def get_many(self, keys: list[str]) -> dict[str, dict]:
        """Several counter rows in one round trip.

        BatchGetItem bills the same RCU as the same number of GetItems and
        costs one network call instead of N. The tenants page issues one
        GetItem per tenant today; this is the shape that replaces it.
        """
        if not keys:
            return {}
        resp = self._table.meta.client.batch_get_item(RequestItems={
            self._table_name: {
                "Keys": [{"date": k} for k in keys],
            }
        })
        rows = resp.get("Responses", {}).get(self._table_name, [])
        return {row["date"]: row for row in rows}
```

- [ ] **Step 4: Add the status function**

In `services/backend/core/usage.py`:

```python
def protection_status(resource, tenant_id: str) -> dict:
    """Whether this tenant's traffic is being measured right now, and why not.

    Three facts from two keys on one table. The global one matters as much as
    the tenant's own: enforce_usage_ceiling refuses ingest platform-wide, so a
    tenant inside its 25% share can still be unmeasured because the day filled
    up elsewhere. The agent sees that as a 429; until now the console saw
    nothing at all.
    """
    today = _today()
    # Reuse the existing key builder rather than formatting the string here.
    # The marker that makes a tenant row uncollidable with the global row is
    # documented next to it, and a second copy of that format is a second
    # place for it to drift.
    tenant_key = _tenant_counter_key(tenant_id, today)
    rows = UsageCountersTable(resource).get_many([today, tenant_key])

    global_used = int(rows.get(today, {}).get("total_requests", 0))
    tenant_used = int(rows.get(tenant_key, {}).get("total_requests", 0))
    tenant_ceiling = tenant_daily_quota()

    reason = None
    if global_used >= _DAILY_REQUEST_CEILING:
        reason = "global"
    elif tenant_used >= tenant_ceiling:
        reason = "tenant"

    return {
        "tenant_used": tenant_used,
        "tenant_ceiling": tenant_ceiling,
        "global_used": global_used,
        "global_ceiling": _DAILY_REQUEST_CEILING,
        "throttled": reason is not None,
        "throttled_reason": reason,
    }
```

Import `_tenant_counter_key` and `tenant_daily_quota` at the top of the
module alongside the existing helpers; both are already defined there.

- [ ] **Step 5: Run it and watch it pass**

Run: `PYTHONPATH=. python -m pytest services/backend/tests/test_protection_status.py -q`
Expected: 5 passed.

`tenant_daily_quota()` and `_tenant_counter_key()` already exist in
`services/backend/core/usage.py` — use them rather than recomputing
`_DAILY_REQUEST_CEILING * 0.25` or re-formatting the key. The share constant
is named `_TENANT_DAILY_SHARE`, and `enforce_tenant_quota` already reads it
through `tenant_daily_quota()`, which is what makes the ceiling the console
shows and the ceiling the agent is refused at the same number by
construction.

- [ ] **Step 6: Raise the table's read capacity**

In `terraform/dynamodb.tf`, find the `UsageCounters` table and change `read_capacity` from 1 to 2, with the reason in a comment:

```hcl
  # 2, not 1: protection_status runs on every console page and issues one
  # BatchGetItem of two keys. At one page view per second that sits exactly on
  # a 1-RCU line and survives only on burst credit. Account headroom is 11 RCU.
  read_capacity = 2
```

- [ ] **Step 7: Validate the Terraform and run everything**

Run: `cd terraform && terraform fmt -check && terraform validate && cd ..`
Then: `PYTHONPATH=. python -m pytest services/backend/tests/ services/agent/tests/ -q --cov=services/backend --cov=services/agent --cov-fail-under=80`
Then: `ruff check services/backend services/agent`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add services/backend/core/tables.py services/backend/core/usage.py \
        services/backend/tests/test_protection_status.py terraform/dynamodb.tf
git commit -m "feat(usage): the console can finally see why a tenant stopped being measured"
```

---

## Done when

- The full suite passes and coverage stays above 80%.
- `ruff check services/backend services/agent` is clean.
- `terraform plan` shows exactly one capacity change and no resource replacement.
- Task 3's browser confirmation has been done, or is recorded as outstanding.

Phase 1 may not begin before Task 4 has shipped: without `last_features` on the episode row, the zoom-1 screen would promise evidence that TTL has already deleted.
