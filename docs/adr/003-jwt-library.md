# ADR-003: PyJWT replaces python-jose for Cognito token verification

- **Date:** 2026-08-24
- **Status:** accepted
- **Supersedes:** the implicit python-jose choice made in Stage 6 of `docs/PLAN.md`
  (it was never recorded as a decision — it was simply what got imported)

## Context

Phase 4's dependency audit found `python-jose` structurally stuck, not merely
out of date:

```
python-jose 3.4.0 requires pyasn1<0.5.0,>=0.4.1
pyasn1 0.4.8  ->  PYSEC-2026-2263, -3455, -3456, -3457  (fixed only in 0.6.3+)
python-jose   ->  also pulls ecdsa 0.19.2, PYSEC-2026-1325, NO fix available
```

The pinned range cannot reach a patched `pyasn1`. Forcing `pyasn1==0.6.4`
anyway does run — the whole suite passed on it — but that is an unsupported
combination: a fresh `pip install -r requirements.txt` resolves the vulnerable
0.4.8 straight back. It is the "half-bump" trap this project already refused
once during the Phase 3 dependency remediation.

This library also verifies **every** token on **every** authenticated request.
It is the single most security-sensitive dependency in the codebase, and
`python-jose` has a track record of JWT-specific CVEs (CVE-2024-33663
algorithm confusion, CVE-2024-33664 JWE decompression bomb).

## Options considered

| Option | Result |
|---|---|
| Keep python-jose, accept the CVEs | 5 known vulnerabilities documented as accepted risk, on the auth path, indefinitely — and `pip-audit` never comes back clean |
| Keep python-jose, force pyasn1 0.6.4 | Unsupported resolution; reverts on any clean install. Rejected as a fake fix |
| **Move to PyJWT** | RS256 verification via `cryptography` alone; no `ecdsa`, no `pyasn1`, no `rsa` in the tree |

## Decision

Use **PyJWT** (`PyJWT==2.13.0`, `cryptography==50.0.0`). JWKS entries are
converted with `jwt.algorithms.RSAAlgorithm.from_jwk`, and verification keeps
every control Phase 4 added: pinned `algorithms=["RS256"]`, mandatory `aud`
and `iss`, `exp` checked, `token_use == "id"` enforced, and a fail-closed
guard when the Cognito pool/app-client are unconfigured.

Cognito signs exclusively with RS256, so nothing of value is lost by dropping
a library that also speaks ECDSA and JWE.

## Consequences

**Good**
- `pip-audit -r services/backend/requirements.txt` → *No known vulnerabilities
  found*. Same for the agent's requirements.
- Three transitive dependencies (`ecdsa`, `pyasn1`, `rsa`) leave the runtime,
  including the one whose advisory has no fix at all.
- Smaller Lambda package; `cryptography` was already present transitively.

**Cost / risk**
- The most security-critical function in the codebase was rewritten. It is
  covered by 14 auth tests that sign real RS256 tokens with a real locally
  generated keypair and verify through the same `jwt.decode()` path production
  uses — including a tampered signature, a wrong signing key, an access token,
  a foreign audience and a foreign issuer. The whole 142-test suite passes.
- PyJWT's API differs from python-jose: a JWK dict must be converted to a key
  object first (`RSAAlgorithm.from_jwk`) rather than passed in raw. Any future
  code touching JWTs must follow that shape.

**Not addressed here**
- The Cognito Hosted-UI OAuth flow is still absent (both the CLI and the web
  UI paste an ID token by hand). That is a separate v1 gap, unchanged by this
  decision.
