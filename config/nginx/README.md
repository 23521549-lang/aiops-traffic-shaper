# Example customer nginx configuration

Not part of the product. This is what a customer puts on **their own machine**
so the agent's nginx adapter has something to act on.

| File | Goes to | Owner |
|---|---|---|
| `nginx.conf` | `/etc/nginx/nginx.conf` | the customer |
| `default.conf` | `/etc/nginx/conf.d/default.conf` | the customer |
| `aiops-agent-geo.conf` | `/etc/nginx/conf.d/aiops-agent-geo.conf` | **seed only** — the agent overwrites it |
| `aiops-agent-deny.conf` | created by the agent on the first hard block | the agent |

## Why the seed file exists

`nginx.conf` builds its Tier 1 rate limit on `$agent_rate_limited`, a variable
defined by the `geo` block the agent writes. **nginx refuses to start when a
`map` references a variable nothing defines** — so on a fresh install, before
the agent has ever enforced anything, nginx would fail to come up at all.

`aiops-agent-geo.conf` here is that block with an empty body: it defines the
variable, flags nobody, and is byte-identical to what the adapter writes when
no IP is rate-limited. The agent replaces it the first time it acts.
`test_nginx_example_config.py` asserts that byte-identity, so renaming the
variable in the adapter breaks a test instead of breaking a customer's nginx.

## What is and is not verified

The coupling between these files and the agent is tested. The configuration
itself has **not** been validated against a running nginx — no `nginx -t` has
been executed on it. Treat it as a reference to adapt and test on your own
system, not as a drop-in.

## History

The previous files here configured the superseded architecture: the rate-limit
list came from a Kubernetes ConfigMap patched by a `worker-orchestrator`
service, and `default.conf` used `${...}` envsubst placeholders from a
docker-compose stack. None of that exists after ADR-002.
