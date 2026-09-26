# ADR-008: Enforcement has an off switch, and it lives on the tenant

- **Date:** 2026-09-25
- **Status:** accepted

## Context

This product turns a customer's own visitors away. Until now it had no way
to stop doing that.

The full list of what a tenant could actually change was: allow one address,
allow a selection, move either gate, register an agent, export the active
set. There was no control that meant "stop". The nearest thing was suspending
the tenant, which is a publisher action, and revoking agent keys, which is a
tenant action with the wrong shape: it stops enforcement by stopping the
telemetry too, so the customer loses the measurements that would tell them
whether stopping was the right call. At 3am, with real customers being
refused, that is not a control. It is a demolition.

The gap was found by drawing the console we wanted and then checking each
button against the route table: 78 of 114 had nothing behind them. Most of
those were theatre. This one was not.

## Decision

A tenant can pause enforcement. Paused:

- the platform **keeps scoring, keeps writing `MitigationState`, and keeps
  writing history**;
- the telemetry response carries `decisions: []`, `active_ips: []` and
  `enforce: false`;
- `GET /agent/v1/decisions` returns `[]` as well;
- every console page carries a banner that cannot be dismissed, and a held
  row says **Watch only** instead of claiming the customer's servers have the
  rule.

The flag is one attribute on the **Tenants** item, `enforce_paused_at`. Its
ABSENCE means enforcing, so every account that existed before this needs no
migration and only accounts that have deliberately paused carry it.

### Why the agent needs no new version

`DecisionStore.reconcile` already releases every local rule that is not in the
served set, because a human allowing an address had to be able to lift a block
before its TTL. An empty served set is therefore already the instruction to
release everything, and the switch takes effect on **agents installed before
it existed**, within one flush interval.

The one subtlety is that `active_ips` must be **present and empty**, never
omitted. `runner._apply` treats an absent key as "the backend cannot tell me",
which is correct during a partial rollout and would silently defeat this
control. A test asserts the key is present.

`enforce: false` is added anyway, and is the only part that wants a newer
agent. It does not change behaviour; it lets the agent's log say *why* it
released, instead of leaving an operator reading what looks like the backend
having lost every decision at once.

### Why the tenant and not the agent

A source is judged for the tenant and refused at every one of their servers,
which is the property that makes this better than a per-machine rule engine.
An off switch scoped per machine would leave a customer blocked on four hosts
and open on the fifth: the worst of both, and impossible to reason about
during the incident that made them reach for it.

### Why the platform keeps measuring

Because the question that follows "I turned it off" is "what did I miss". If
the platform stopped judging, the answer would be a gap, and the customer
deciding whether to turn it back on would have nothing to decide with. It
costs nothing extra: the batch has already been paid for by the time the
scoring runs.

The honesty cost is real and is paid on screen. A row that says
`3 of 3 written` is a true sentence about collection times and a false one
about the world the moment nothing is enforced, so `reach()` returns
**Watch only** while paused, and the CSV export carries the same caveat,
because the file outlives the screen it came from.

### Audited

Turning protection off is the most consequential thing a tenant can do in
this console, so it is written to the settings ledger with the actor. "Who
switched it off, and when" is the first question the review after an incident
asks.

## Consequences

- One GetItem on `Tenants` per console page render, resolved in `_shell` so
  no page can forget it. The console is not polled, so this is small; the
  alternative was a screen reporting five healthy agents while the customer's
  servers enforced nothing.
- `list_decisions` now takes the whole authenticated agent rather than the
  tenant id, because the tenant item it needs is already in that dict and
  re-reading it would add a GetItem to the agent's poll. Two direct callers
  in tests pass it by hand, which is this codebase's standing rule for
  calling a route as a function.

## What this does not do

It does not release a single bad block. Allowing the address already does
that, through the same reconcile path. A separate "flush local rules" control
was drawn and then dropped for that reason: it would repair an agent whose
local state has drifted from the platform, which is a rarer problem than the
drawing implied.

## Follow-up: rolling back a model is not affordable yet

The next control we wanted was "roll back to the previous model", for the case
where a nightly retrain starts refusing real customers. It cannot be built on
today's schema: `Models` holds exactly two rows per tenant, `staging` and
`production`, and promotion **overwrites** production, so the outgoing model
is gone.

Keeping a third copy is not free. `Models` is provisioned at **1 WCU** and a
serialised model is ~238 KB, which is 238 write units for one item; the
nightly already writes two of them and leans on burst capacity to do it.

The design that fixes it is cheaper than what we run today: write each model
under its own version key and keep a **pointer item of a few bytes** naming
the live one. Promotion becomes a pointer write instead of a 238 KB copy,
rollback becomes the same, and the nightly writes one blob instead of two.
It touches the scorer's read path, so it is its own change and its own ADR.
