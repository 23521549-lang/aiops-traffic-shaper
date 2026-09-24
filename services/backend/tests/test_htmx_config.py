"""htmx keeps working URLs without persisting tenant markup.

`historyEnabled:false` was chosen to stop htmx writing page HTML into
localStorage, where it would be tenant-scoped markup on a possibly shared
machine. That reason is sound and the flag was too broad: it also disabled
hx-push-url, so a swap left the address bar stale and a pasted link
reproduced a different view than the one on screen - destroying exactly the
property the console deliberately bought with `?ip=`.

`historyCacheSize:0` addresses the stated concern directly. In the vendored
file the save function short-circuits and returns before any write, and
removes a cache left by an earlier build.
"""
import json
import re
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[1] / "ui" / "templates"
_STATIC = Path(__file__).resolve().parents[1] / "ui" / "static"


def _config() -> dict:
    html = (_TEMPLATES / "base.html").read_text(encoding="utf-8")
    raw = re.search(r"""name="htmx-config" content='([^']+)'""", html).group(1)
    return json.loads(raw)


def test_urls_are_pushed_so_a_pasted_link_shows_what_was_on_screen():
    assert _config()["historyEnabled"] is True


def test_no_page_markup_is_persisted_to_localstorage():
    """The security property the original flag was chosen for, kept."""
    assert _config()["historyCacheSize"] == 0


def test_the_vendored_htmx_actually_honours_a_zero_cache_size():
    """Read the bytes, not the docs. The config above is only meaningful if
    the shipped build short-circuits on it - and this build removes the
    cache an earlier build may have left behind, which is a bonus nobody
    claimed."""
    js = (_STATIC / "htmx.min.js").read_text(encoding="utf-8", errors="replace")

    assert 'historyCacheSize<=0){localStorage.removeItem("htmx-history-cache");return' in js


def test_the_other_lockdowns_are_untouched():
    """These three are independent of the history flags and each removes a
    capability the CSP would otherwise have to allow."""
    config = _config()

    assert config["allowEval"] is False
    assert config["allowScriptTags"] is False
    assert config["includeIndicatorStyles"] is False
