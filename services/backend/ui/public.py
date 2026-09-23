"""The public surface. Everything here is anonymous, static and cacheable.

Until now the entire public surface was a 302 from `/` to a login form that
asks for a pasted JWT. Not one comparable product sends an anonymous visitor
to a credential field, and for a product whose whole value is a screen, the
landing page is the screen.

Three properties matter and they are all consequences of one decision —
**these routes read zero DynamoDB**:

  * They can sit behind a CloudFront cache behaviour, so being hammered
    costs nothing. That is a better answer than rate limiting, which this
    architecture cannot afford anyway (ADR-002 dropped API Gateway).
  * They cannot leak tenant data, because they never load any.
  * They stay unmetered (`_UNMETERED_PATH_PREFIXES` in main.py), so an
    anonymous visitor cannot spend the free-tier write budget.

The illustrations are the console's own chart macros fed representative
numbers rather than bespoke marketing graphics. That is deliberate: a
landing page whose pictures are the real components cannot drift away from
the product it is selling.
"""
from fastapi import APIRouter, Cookie, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from services.backend.ui.charts import deviation_chart, sigma_strip
from services.backend.ui.templates_env import templates

router = APIRouter()

THEME_COOKIE = "theme"
_THEMES = ("light", "dark", "system")

# Representative, not live. Shaped from the real production case in ADR-006:
# a quiet day with one brute force that reached 5.4 sigma and one milder
# abuser at 4.6. Hardcoded so the page needs no database and no clock.
_HERO_SHAPE = [
    1.1, 0.8, 1.4, 0.9, 1.2, 0.7, 1.6, 1.0, 0.9, 1.3, 1.1, 0.6,
    1.5, 0.8, 1.2, 1.0, 0.7, 1.4, 1.1, 0.9, 4.6, 1.3, 1.0, 0.8,
    1.2, 1.5, 0.9, 1.1, 0.7, 1.3, 1.0, 1.4, 0.8, 1.2, 5.4, 1.1,
    0.9, 1.3, 1.0, 0.7, 1.5, 1.1, 0.8, 1.2, 0.9, 1.4, 1.0, 1.1,
]


def _hero_points():
    """48 buckets of 30 minutes, oldest first."""
    return [(i * 1800, -v) for i, v in enumerate(_HERO_SHAPE)]


def _hero_label(ts: int) -> str:
    hours_ago = 24 - ts // 3600
    if hours_ago >= 24:
        return "−24h"
    if hours_ago <= 0:
        return "now"
    return f"−{hours_ago}h"


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, id_token: str | None = Cookie(default=None)):
    """The front door.

    A signed-in visitor is sent to their console rather than being shown a
    pitch for a product they already use — but the check is the mere
    PRESENCE of the cookie, not its validity. Verifying it here would mean a
    JWKS fetch on an anonymous page, which would make the page uncacheable
    and give an unauthenticated caller a lever on a network round trip. An
    expired cookie lands on the console and gets redirected back to login
    one hop later, which is the correct outcome by a longer route.
    """
    if id_token:
        return RedirectResponse(url="/dashboard/ui", status_code=302)

    chart = deviation_chart(
        _hero_points(),
        caption="24 hours of one site's traffic, measured against its own baseline.",
        label_for=_hero_label,
    )
    return templates.TemplateResponse(request, "landing.html", {
        "chart": chart,
        "strip": sigma_strip(5.4),
        "theme": request.cookies.get(THEME_COOKIE, ""),
    })


@router.post("/ui/prefs/theme")
def set_theme(response: Response, theme: str = "system",
              next_url: str = "/"):
    """Theme as a cookie, rendered by the server into `data-theme`.

    This is the only implementation possible under `script-src 'self'` with
    no inline script. The usual approach is a small `<script>` in `<head>`
    that reads localStorage before first paint; there is nowhere to put it.
    A server-rendered attribute is also strictly better — there is no flash
    of the wrong theme at all, and it works with JavaScript disabled.
    """
    theme = theme if theme in _THEMES else "system"
    # Only ever a same-origin path: an open redirect on an unauthenticated
    # endpoint is a phishing primitive.
    target = next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"

    redirect = RedirectResponse(url=target, status_code=303)
    if theme == "system":
        redirect.delete_cookie(THEME_COOKIE)
    else:
        redirect.set_cookie(THEME_COOKIE, theme, max_age=31_536_000,
                            samesite="lax", secure=True, httponly=True)
    return redirect
