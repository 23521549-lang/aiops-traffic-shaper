"""Phase 4 / M7 — CSRF protection for the cookie-authenticated UI.

Stage 9 introduced cookie auth so plain browser navigation works, which also
introduced classic CSRF exposure: the browser attaches that cookie to
cross-site requests too. `SameSite=Lax` blocks the cross-site form POST in
current browsers, but it was the ONLY thing standing in the way — nothing the
application itself verified. This adds the double-submit token that vibesec
calls for as defence in depth: a random value in a JS-readable cookie that the
page must echo back in a header a cross-site form is incapable of setting.

That reasoning was originally extended to exempt the whole JSON API, on the
grounds that `/dashboard/v1/*` and `/admin/v1/*` "authenticate on an
`Authorization: Bearer` header, which a cross-site form also cannot set".
That was wrong, and the portal review caught it. `dashboard_auth` and
`admin_auth` (api/cognito_auth.py) also accept `id_token` from a Cookie, so a
browser holding the login cookie authenticates against the JSON API too —
leaving `POST /admin/v1/tenants/{id}/suspend` defended by SameSite=Lax alone,
which is precisely what the paragraph above says is not enough.

`verify_csrf_if_cookie_auth` closes that without breaking anyone: a caller
presenting its credential in a HEADER is not a browser form and is exempt
(the agent CLI, curl, and the portal's own X-Id-Token fetches under ADR-005);
a caller authenticating by COOKIE alone must echo the token.
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


def verify_csrf_if_cookie_auth(
    authorization: str | None = Header(default=None),
    x_id_token: str | None = Header(default=None),
    csrf_token: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None),
) -> None:
    """CSRF protection for the JSON API, applied only where it can apply.

    A request carrying its credential in a header was not produced by a
    cross-site form — that is the one part of the original exemption that
    was always true, and it keeps every non-browser client working. A
    request whose only credential is the cookie gets the same double-submit
    check the HTML UI has had since Phase 4.
    """
    if authorization or x_id_token:
        return
    verify_csrf(csrf_token=csrf_token, x_csrf_token=x_csrf_token)
