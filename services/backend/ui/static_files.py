"""Static assets, served from an explicit allow-list.

A directory-mounted `StaticFiles` would take the path from the URL. These
routes take it from a dict, so there is no user-controlled component in any
filesystem path and path traversal is not a class of bug that can occur
here. There are three files; a mount would be more machinery for less
safety.

`Cache-Control: max-age=300` exists for a specific reason (ADR-007): once
CloudFront caches `/ui/static/*`, a cached asset does NOT roll back when the
Lambda alias is repointed. Five minutes bounds how long a rolled-back
deployment can serve the previous stylesheet. Without it the only way back
would be an invalidation, which is the one step in this project that the
alias cannot undo.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

_STATIC_DIR = Path(__file__).parent / "static"

# Filename -> media type. The key is matched exactly; nothing is globbed.
#
# Adding a <script src> to a template is one edit and adding it here is a
# second, in another file, and for a while nothing connected them. Two
# scripts shipped referenced-but-not-served, and the failure is silent in
# the worst way: the page renders, the markup is all there, and the feature
# is dead because the browser got a 404. test_static_assets.py now compares
# this dict against every template, in both directions.
_ASSETS = {
    "app.css": "text/css",
    "console.css": "text/css",
    "htmx.min.js": "application/javascript",
    "signed-post.js": "application/javascript",
    "ui-status.js": "application/javascript",
    "bulk-select.js": "application/javascript",
    "keys.js": "application/javascript",
}

CACHE_CONTROL = "public, max-age=300"


@router.get("/ui/static/{filename}")
def static_asset(filename: str) -> FileResponse:
    media_type = _ASSETS.get(filename)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(
        _STATIC_DIR / filename,
        media_type=media_type,
        headers={"Cache-Control": CACHE_CONTROL},
    )
