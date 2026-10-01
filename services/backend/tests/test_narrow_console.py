"""What the console loses on a phone, and must not.

Measured once at a 390px viewport, on the tenant's Protection page:

  Protection       right edge  217
  History                      280
  Agents                       342
  Allowed IPs                  410   past the 390px screen
  Detection model              488   past the 390px screen

The nav strip already carried `overflow-x: auto`, so it looked fixed and was
not: a flex item defaults to `min-width: auto`, an item that refuses to
shrink below its content never overflows, and the scroll therefore never
engaged. Allowed IPs is where a customer lifts a block on a real visitor, and
it was simply unreachable.

The second failure was blunter. Sign out lived in a sidebar footer that was
`display: none` below the breakpoint, so a phone had no way to end a session.

Both guarantees survived the shell being rewritten, and the shapes they are
asserted against did not:

  the scroll moved from `.c-nav-group` to `.c-nav`, which is now the strip
  itself rather than a box inside one;

  Sign out moved out of the sidebar entirely and into the full-width top bar,
  which is the stronger fix - it is no longer inside anything a media query
  could hide, so the failure cannot recur in that shape at all.

Asserted against the stylesheet rather than a browser, because the suite has
no browser and these are one-line rules whose absence is the whole bug.

Everything above checks what the narrow rules SAY. A screenshot at 390px
later showed the console still wearing its desktop layout with every one of
those rules present and correct, because a rule that says the right thing
and loses the cascade is indistinguishable, from here, from one that is not
there. Two ways it lost, both introduced when the sheet was scoped under
`.console`:

  the override was written bare (`.nav`) against a scoped base
  (`.console .nav`), so it lost on specificity, 0,1,0 against 0,2,0;

  and once both were scoped, the media query still sat a third of the way up
  the file, above the base rules it meant to override, so it lost on source
  order instead.

The last two tests are those two failures, stated so the next edit cannot
reintroduce either one quietly.
"""
import re
from pathlib import Path

import pytest

_UI = Path(__file__).resolve().parents[1] / "ui"
_CSS = _UI / "static" / "console.css"
_SHELL = _UI / "templates" / "app_shell.html"


def _strip(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


@pytest.fixture(scope="module")
def narrow() -> str:
    """The body of the narrow-width media query, comments stripped.

    Found by its content rather than by an exact pixel string: the
    breakpoint is a design decision and may move, the guarantees inside it
    may not.
    """
    css = _strip(_CSS.read_text(encoding="utf-8"))
    for match in re.finditer(r"@media\s*\(max-width:[^)]*\)", css):
        start = match.start()
        depth = 0
        for i in range(css.index("{", start), len(css)):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    block = css[start:i]
                    if ".nav" in block:
                        return block
                    break
    raise AssertionError("no narrow media query touches the navigation")


def _rule(block: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*(?:,[^{]*)?\{([^}]*)\}", block)
    return match.group(1) if match else ""


def test_the_nav_can_scroll_to_its_last_item(narrow):
    """`overflow-x: auto` does nothing until the box is allowed to be
    narrower than its contents."""
    rule = _rule(narrow, ".nav")

    assert "overflow-x" in rule, "the nav strip has to scroll on a phone"
    assert "min-width" in rule, (
        "a grid or flex child defaults to min-width: auto, so it never "
        "shrinks below its content and the overflow-x above never engages; "
        "Allowed IPs and Detection model sat past the right edge of a 390px "
        "screen with no way to reach them")


def test_signing_out_is_possible_on_a_phone():
    """It lives in the top bar now, which no media query hides. Asserted
    against the markup rather than the stylesheet, because the fix is that
    there is no longer a hideable box around it."""
    shell = _SHELL.read_text(encoding="utf-8")
    topbar = shell[shell.index('class="bar"'):shell.index("</header>")]

    assert "/ui/logout" in topbar, (
        "Sign out must sit in the top bar; inside the sidebar it was one "
        "display:none away from being unreachable on a phone, and once was")


def test_nothing_the_narrow_layout_hides_is_the_only_way_to_do_something():
    """A label may go. A control may not. This is the rule the old footer
    broke, stated so it cannot be broken again in a different shape."""
    css = _strip(_CSS.read_text(encoding="utf-8"))
    hidden = {m.group(1).strip()
              for m in re.finditer(r"([^{}]+)\{[^}]*display:\s*none[^}]*\}", css)}

    for selector in hidden:
        for control in (".btn", ".nav a", ".theme-toggle", "[data-palette-open]"):
            assert control not in selector, (
                f"{selector} hides a control outright; hide a label instead")


def test_the_top_bar_wraps_rather_than_overflowing():
    """It carries the tenant id, three theme buttons, the palette and Sign
    out. On a 390px screen those cannot sit on one line, and the one that
    would fall off the end is the last one."""
    css = _strip(_CSS.read_text(encoding="utf-8"))
    rule = _rule(css, ".console .bar")

    assert "flex-wrap" in rule


def test_the_page_body_can_shrink_below_its_content():
    """The same min-width:auto trap, one level up: a grid child that refuses
    to shrink pushes the whole page sideways instead of scrolling its own
    table."""
    css = _strip(_CSS.read_text(encoding="utf-8"))

    assert "min-width: 0" in _rule(css, ".console .main")


# --- and that the rules win ----------------------------------------------

_SELECTOR = re.compile(r"(?:^|\})\s*([^{}@][^{}]*?)\s*\{", re.M)


def _selectors(block: str) -> list[str]:
    out = []
    for match in _SELECTOR.finditer(block):
        for part in match.group(1).split(","):
            part = " ".join(part.split())
            if part:
                out.append(part)
    return out


def _outside_media(css: str) -> str:
    """The sheet with every @media block blanked to spaces, so positions
    still line up with the original text."""
    out = list(css)
    for match in re.finditer(r"@media", css):
        depth = 0
        for i in range(css.index("{", match.start()), len(css)):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    for j in range(match.start(), i + 1):
                        out[j] = " "
                    break
    return "".join(out)


def test_every_narrow_override_outranks_the_rule_it_overrides(narrow):
    """Same specificity means the later rule wins, so the narrow block has to
    come after every base rule it is trying to beat. It did not, and a phone
    silently got the desktop grid."""
    css = _strip(_CSS.read_text(encoding="utf-8"))
    base = _outside_media(css)
    start = css.index(narrow)

    for selector in _selectors(narrow):
        last = base.rfind(selector + " {")
        if last == -1:
            last = base.rfind(selector + "{")
        if last == -1:
            continue
        assert last < start, (
            f"`{selector}` is defined at {last} and overridden at {start}, "
            f"which is EARLIER in the file. Same specificity, so the base "
            f"rule wins and the narrow layout never applies")


def test_no_narrow_override_is_less_specific_than_its_base(narrow):
    """The other way it lost: a bare `.nav` cannot override `.console .nav`
    however late it is written."""
    css = _strip(_CSS.read_text(encoding="utf-8"))
    base = _outside_media(css)

    for selector in _selectors(narrow):
        if ".console" in selector:
            continue
        scoped = ".console " + selector.lstrip()
        assert scoped not in base, (
            f"`{selector}` is overridden bare while the base rule is "
            f"`{scoped}`; 0,1,0 never beats 0,2,0 and this override is dead")


def test_the_shell_selector_is_compound_and_not_descendant():
    """`<div class="console app">` carries both classes on ONE element, so
    `.console .app` matches nothing at all. Scoping the sheet turned the
    shell's own rule into a descendant selector and the shell lost its
    grid."""
    shell = _SHELL.read_text(encoding="utf-8")
    css = _strip(_CSS.read_text(encoding="utf-8"))

    assert 'class="console app"' in shell
    assert ".console .app" not in css, (
        "the shell is one element with two classes; this has to be "
        "`.console.app`, compound, or it selects nothing")
    assert ".console.app" in css
