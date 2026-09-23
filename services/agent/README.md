# traffic-shaper-agent

Reads your nginx access log, sends per-IP counts to the backend, and applies
the decisions that come back.

It is deliberately thin. All of the machine learning lives in the backend
(ADR-002), so this depends on `click` and the standard library: no boto3, no
numpy, no requests. It can be installed on a web server without an argument
about dependencies.

## What leaves your machine

Counts, per source IP, per five-second bucket: request count, error count,
POST count, total bytes, total time, distinct URI count, distinct user-agent
count. No URLs, no bodies, no headers, no cookies.

## Install

    pip install traffic-shaper-agent

Sign in to the console, open **Agents**, and add one. It prints the two
commands below with your values already filled in. The API key is shown once,
because only its hash is stored.

    traffic-shaper connect       --backend-url https://<your-deployment>       --tenant-id <from the console>       --agent-id <from the console>       --api-key <from the console>

    traffic-shaper run

Credentials are written to `~/.aiops-agent/config.json`, owner-readable only.

## The access log

The agent reads JSON, not combined format, because the backend needs the
request method and URI as separate fields and combined format does not carry
them that way. `config/nginx/nginx.conf` in the backend repository has the
`log_format` to copy; it uses `escape=json`, so a request URI containing a
quote cannot break the line.

## What it does while running

Batches metadata, posts it, applies any decisions to nginx and iptables
(whichever are available; both, if both are), and removes each block when its
timer runs out. Nothing expires on its own in either enforcer, so the agent
staying alive is what lets a blocked visitor back in. It survives the backend
being unreachable for exactly that reason.

Nothing is enforced until your first model is trained, which takes one night.

## Commands

    traffic-shaper connect    save credentials issued in the console
    traffic-shaper register   mint an agent from the CLI, using an ID token
    traffic-shaper run        read the log, send, enforce
    traffic-shaper status     show what this machine is registered as
