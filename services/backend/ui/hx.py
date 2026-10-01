"""Where a mutation sends the operator next.

Two shapes of action live in this console and they need opposite answers.

A button standing next to the thing it changes swaps that thing in place:
the allowed-list table, the tenant table. The operator is already looking at
the result, and a full page load would lose their scroll position and their
place in the keyboard order.

A button standing somewhere else cannot do that. The Allow button in the
mitigation table posts with hx-swap="none", which by design throws the
response away, so a rendered table came back to nothing and the source
stayed on screen still labelled Blocked. A button that appears to do nothing
gets pressed again.

The destination is chosen from a fixed map rather than read out of the
request. Threading a return path through the query string would turn every
action button in the product into an open redirect, and these are POSTs an
attacker can aim with a plain link.
"""
from fastapi import Response


def hx_return(destination: str | None, choices: dict[str, str]) -> Response | None:
    """An empty 200 carrying HX-Redirect, or None to render normally.

    htmx follows HX-Redirect with a full navigation, so the page that
    re-renders is the real one: same auth, same query, same server-rendered
    state. Nothing about the new page has to be guessed client-side.
    """
    target = choices.get(destination or "")
    if not target:
        return None
    return Response(status_code=200, headers={"HX-Redirect": target})
