"""The JavaScript this product ships has to parse.

`keys.js` shipped with `/["\\]/g` mangled down to `/["\]/`, which is not a
valid regular expression: the backslash escapes the closing bracket so the
character class never closes. The browser refused the whole file, so the
command palette did not open on Ctrl+K and did not open from its own button
either. Nothing in the suite noticed, and neither did a screenshot pass that
listened for `console` messages, because a parse failure arrives as
`pageerror` instead.

A syntax check is cheap and it is the exact shape of the defect. Node is on
every CI runner and on this machine; where it is genuinely absent the test
says so rather than passing quietly, because a skipped guard that looks green
is how the first one got through.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parents[1] / "ui" / "static"
_OURS = sorted(p for p in _STATIC.glob("*.js") if p.name != "htmx.min.js")
_NODE = shutil.which("node")


def test_there_are_scripts_to_check():
    """A rename or a move must not turn this file into a no-op."""
    assert _OURS, f"no first-party scripts found in {_STATIC}"


@pytest.mark.skipif(_NODE is None, reason="node is not installed here")
@pytest.mark.parametrize("path", _OURS, ids=lambda p: p.name)
def test_the_script_parses(path):
    result = subprocess.run([_NODE, "--check", str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, (
        f"{path.name} does not parse, so the browser discards the entire "
        f"file and every feature in it is silently dead:\n{result.stderr}")


@pytest.mark.skipif(_NODE is None, reason="node is not installed here")
@pytest.mark.parametrize("path", _OURS, ids=lambda p: p.name)
def test_the_script_is_strict_and_wrapped(path):
    """Every one of these runs on the same page in file order. A stray
    global from one is a variable the next can overwrite."""
    text = path.read_text(encoding="utf-8")
    assert '"use strict"' in text, f"{path.name} is not strict"
    assert text.lstrip().startswith("/*") or text.lstrip().startswith("("), (
        f"{path.name} does not open with a comment or an IIFE")
    assert "(function" in text, f"{path.name} leaks its scope to the page"
