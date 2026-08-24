"""Phase 4 / M7 — CSRF protection for the cookie-authenticated UI.

Stage 9 introduced cookie auth so plain browser navigation works, which also
introduced classic CSRF exposure: the browser attaches that cookie to
cross-site requests too. `SameSite=Lax` blocks the cross-site form POST in
current browsers, but it was the ONLY thing standing in the way — nothing the
application itself verified. This adds the double-submit token that vibesec
calls for as defence in depth: a random value in a JS-readable cookie that the
page must echo back in a header a cross-site form is incapable of setting.

Deliberately NOT applied to the JSON API (`/dashboard/v1/*`, `/admin/v1/*`,
`/agent/v1/*`): those authenticate on an `Authorization: Bearer` header, which
a cross-site form also cannot set, so they are not CSRF-reachable. Requiring a
token there would break the agent CLI and every other non-browser client for
no security gain.
"""
import secrets

from fastapi import Cookie, Header, HTTPException

class CsrfError(HTTPException):
    """Distinct from an auth failure on purpose. main.py redirects 401/403 on
    UI pages to the login screen, which would be actively misleading here: the
    user IS logged in, the request just didn't prove it came from our page.
    Bouncing them to a login form would look like a session problem and invite
    a pointless re-login."""


CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf(csrf_token: str | None = Cookie(default=None),
                x_csrf_token: str | None = Header(default=None)) -> None:
    """Reject unless the header echoes the cookie exactly. Missing token is a
    rejection, never a pass — a check that only runs when the token happens to
    be present protects nothing."""
    if not csrf_token or not x_csrf_token:
        raise CsrfError(status_code=403, detail="CSRF token missing")
    if not secrets.compare_digest(csrf_token, x_csrf_token):
        raise CsrfError(status_code=403, detail="CSRF token mismatch")
