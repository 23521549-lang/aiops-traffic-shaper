# ADR-007: The portal is rebuilt on htmx, and the CSP gets tighter doing it

- **Date:** 2026-09-23
- **Status:** accepted
- **Supersedes:** the UI row of [ADR-002](002-tech-stack-hybrid.md) and the
  hand-written-helper decision recorded in `docs/PLAN.md`.
- **Amends:** [ADR-005](005-cloudfront-oac.md)'s statement that caching is
  disabled for every path.

## Context

A four-way review of the portal — requirements, UX, architecture,
accessibility — turned up eleven defects that were each confirmed against the
running code. Only some of them were about how the portal looked.

The ones that mattered were not cosmetic:

- **Whitelisting an IP reported success and did not unblock it.** The
  whitelist was consulted at scoring time only, so the `MitigationState` row
  already written survived and the customer's nginx kept the deny in place
  for up to an hour. The product's only "you got this wrong" lever did
  nothing the user could observe.
- **The Control Platform's Agents card could never show anything.**
  `last_seen_at` was written once at registration and never again; nothing
  ever set `status` to `"stale"`; and the card opened on `status="stale"`.
  Two tests asserted this worked, by writing that impossible status into the
  table by hand.
- **Every failed request was either silent or pasted into the page.**
  `interactions.js` never checked `resp.ok`, so a 403 rendered CloudFront's
  error document inside a table and a network failure rendered nothing at
  all. An operator could suspend a tenant, have it fail, and not find out.
- **The JSON API's CSRF exemption rested on a false premise.**
  `csrf.py` said those routes authenticate only on an `Authorization`
  header. They also accept the `id_token` cookie.

Those are fixed separately, each with its own tests. This ADR records the
decision about the layer they were all found in.

### What the UI layer actually was

`services/backend/ui/static/interactions.js`, 143 lines, is a partial
reimplementation of htmx: it reads `hx-get`/`hx-post`/`hx-delete`, swaps
fragments, and adds the `x-amz-content-sha256` header ADR-005 requires. It
was written that way for a stated reason — the development environment could
not fetch and verify the real library.

**That constraint no longer holds.** Measured on 2026-09-23:
`https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js` returns HTTP 200,
50,917 bytes, SHA-256
`e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447`,
16,326 bytes gzipped.

The decision was right when it was made. Leaving it in place after its
premise expired is how a good decision turns into debt, and the file had
already started growing the shape of the library it was standing in for:
`hx-swap` is declared in four templates and read by nothing, `.htmx-swapping`
is defined in CSS and applied by nothing, and the three tests covering the
file are `grep`s over its source text that pass if the logic is inverted.

## Decision

**Adopt htmx 2.0.4, vendored and pinned. Keep Jinja2 server rendering. Keep
one ~30-line shim for the single request that structurally cannot use it.**

### Why a shim still exists

ADR-005 requires the SHA-256 of the request body in a header. htmx cannot
produce it:

- `crypto.subtle.digest` is Promise-only; browsers have no synchronous
  SHA-256.
- htmx issues requests through `XMLHttpRequest`, and its only
  header-mutation hook, `htmx:configRequest`, fires **synchronously**.
- Its `encodeParameters` extension hook owns the wire bytes but is also
  synchronous.
- Re-serialising the body to hash it is a trap this project already refused
  once: ADR-005 hashes "the exact bytes on the wire rather than a
  re-serialisation", and the two encoders genuinely differ — htmx renders a
  space as `%20`, `URLSearchParams` renders it as `+`. The first whitelist
  reason containing a space would 403 at the edge, invisibly to the
  application.

The alternative is bundling a hand-rolled synchronous SHA-256 — a hundred-odd
lines of unowned crypto to avoid thirty lines of adapter. That is a worse
trade, not a better one.

**But only one request needs it.** Of the portal's six interaction sites,
four carry no body at all (suspend, reactivate, remove-whitelist, and the
agent filter — all path or query parameters), and add-whitelist moves to
query parameters. That leaves the login POST, whose body is a Cognito ID
token and must stay a body: a token in a query string lands in CloudFront
access logs, `Referer` headers and browser history.

So the obligation collapses from "every mutation" to "one request", and gets
one file with one caller.

### htmx is configured down from its defaults

Via `<meta name="htmx-config">`, which is markup and needs no CSP allowance:

| Setting | Default | Here | Why |
|---|---|---|---|
| `allowScriptTags` | `true` | **`false`** | htmx executes `<script>` in swapped content. `innerHTML` does not. Taking the default would make the swap path strictly weaker than what it replaces. |
| `allowEval` | `true` | **`false`** | Removes the `new Function` path entirely; `script-src 'self'` then holds without `'unsafe-eval'`. |
| `historyCacheSize` | `10` | **`0`** | htmx caches page HTML in `localStorage`. In a multi-tenant portal that is tenant-scoped markup persisted on a possibly-shared machine. At `0` the save function returns before any write, and removes a cache an earlier build left behind. |
| `historyEnabled` | `true` | `true` | Left on. See the amendment below. |
| `includeIndicatorStyles` | `true` | **`false`** | Stops htmx injecting an inline `<style>`, which is what makes the CSP tightening below possible. |

The adopted surface is smaller than the library's defaults. Adoption here is
a net reduction in exposed behaviour.

#### Amended 2026-09-24 — `historyEnabled` false was too broad

The row above originally read `historyEnabled` = **`false`**, justified by
the `localStorage` concern. That concern is real and `historyCacheSize: 0`
addresses it directly: in the vendored build the save function is

```js
historyCacheSize<=0){localStorage.removeItem("htmx-history-cache");return
```

so nothing is ever written, and a cache from an earlier build is actively
removed. The two settings are independent.

`historyEnabled: false` also disabled `hx-push-url`, which cost the console
the one property it deliberately bought with `?ip=`: that a view can be
pasted into a ticket and reproduce what was on screen. A swap left the
address bar stale and the link showed something else, silently.

This is not a reversal. It is the same decision reached with a more precise
flag, and it obtains the stated security property at no cost.

Two consequences recorded here rather than discovered later. `hx-boost`
becomes functional; it is opt-in per element, so nothing changes unless the
attribute is added, and adding it must be an explicit decision rather than a
side effect. And back-navigation is now a Lambda invocation **and a metered
`UsageCounters` write** — `/dashboard/ui` is not in
`_UNMETERED_PATH_PREFIXES`, so back-button use is write traffic against the
25 WCU envelope.

The rule below — that config overrides are confirmed in a browser at
rollout rather than inferred from the minified source — applies to this
change and has not yet been discharged.

### The CSP gets tighter, not looser

`base.html` carried 116 lines of inline `<style>`, and `main.py` said so in a
comment: *"The inline `<style>` block in base.html is why style-src needs
it."* Extracting it to `/ui/static/app.css` lets `style-src` drop from
`'self' 'unsafe-inline'` to `'self'`.

A redesign that touches every line of that CSS anyway and leaves the
directive loose would be leaving a security improvement on the floor.

### Static assets get a cache behavior

ADR-005 disabled caching for every path, reasoning that "every response is
either tenant-scoped or a mitigation decision". A stylesheet is neither. One
`ordered_cache_behavior` for `/ui/static/*` against the existing Lambda
origin — no S3 bucket, no second origin — removes one uncounted Lambda
invocation per asset per page view. (`main.py` exempts `/ui/static` from
usage metering, so the free-tier card was under-reporting real invocations.)

`Cache-Control: max-age=300` bounds the one property this costs: a cached
asset does not roll back when the Lambda alias is repointed.

## Alternatives rejected

**Keep the hand-written helper.** The choice is not "144 lines versus a
dependency" — it is "a dependency versus a bespoke library that has already
started growing". Fixing the response-code routing, the error surface and the
swap-style dispatcher by hand means reimplementing, untested, three things
htmx's config already does.

**Build an SPA against the existing JSON API.** Rejected on the test
argument, which is stronger than the cost one. The UI is tested by asserting
on server-rendered HTML, and one of those assertions —
`assert "9.9.9.9" not in resp.text` — *is* the tenant-isolation guarantee the
whole product rests on. Moving rendering into JS deletes ~179 lines of
well-covered Python, so **coverage would go up while that guarantee quietly
stopped being checked**. Recovering it needs a Node toolchain in CI, a second
dependency tree `pip-audit` cannot audit, and a second thing to keep green.

Cost was *not* the reason: a UI bundle on S3 would be ~100KB against an
artifact bucket this project already accepts (ADR-004, "under one US cent per
month"). Saying otherwise would have been inflating an objection.

## Consequences

**The package grows by 44KB** — htmx minus the file it replaces, 0.018% of
the 250MB limit. 87% of that limit is scikit-learn's dependency closure;
anyone arguing this decision on package size is arguing about 0.2% while
ignoring the 87%.

**Three defects are fixed by adoption rather than by work.** htmx's default
`responseHandling` does not swap 4xx/5xx bodies and raises
`htmx:responseError`; `hx-swap` starts being honoured; `.htmx-swapping`
starts being applied.

**A supply-chain control appears where there was none.** The vendored bytes
are pinned by a SHA-256 assertion in the existing pytest suite. The repo has
no JS tooling and `pip-audit` cannot see JavaScript, so this is now the
strongest check on frontend dependencies in the project — and it runs in CI
already.

**`csrf_token` no longer needs to be readable by script.** It was
non-`httponly` only so `interactions.js` could parse `document.cookie`. htmx
reads it from a server-rendered `hx-headers` attribute instead.

**One step is not instantly reversible.** Everything here rolls back by
repointing the Lambda alias, except the CloudFront cache behavior:
distribution changes take minutes to propagate and cached objects survive an
alias repoint. It ships alone, after everything else is stable, and never
bundled with a code change.

**htmx's runtime behaviour was verified by reading its bytes, not by running
it.** Every claim above about offsets, defaults and hooks comes from the
downloaded file. The config overrides in particular are confirmed in a
browser during rollout, not assumed.
