/* Keyboard, and nothing about any particular page.
 *
 * The console has been printing `Close Esc` and `Allow this source A` since
 * the panes shipped, with no handler behind either. A hint in an interface
 * is a claim about what the software does; an operator who presses Esc over
 * an open pane and gets silence learns to stop trusting every other hint on
 * the screen too.
 *
 * So the binding lives in the markup next to the hint, as data-key, and
 * this file knows no page-specific keys at all. A page cannot advertise a
 * shortcut without declaring it, because the two are the same edit, and a
 * test holds that line.
 */
(function () {
  "use strict";

  var palette = document.getElementById("palette");

  /* A key press must never be stolen from something the user is typing in,
   * and that includes the palette's own filter box. */
  function isTyping(el) {
    if (!el) return false;
    var tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT"
      || el.isContentEditable;
  }

  function items() {
    if (!palette) return [];
    return Array.prototype.slice.call(
      palette.querySelectorAll("[data-palette-item]"))
      .filter(function (a) { return !a.parentNode.hidden; });
  }

  function filter(text) {
    var needle = text.trim().toLowerCase();
    var list = palette.querySelectorAll("[data-palette-item]");
    var shown = 0;
    Array.prototype.forEach.call(list, function (a) {
      var hit = !needle || a.textContent.toLowerCase().indexOf(needle) !== -1;
      a.parentNode.hidden = !hit;
      if (hit) shown++;
    });
    var empty = palette.querySelector("[data-palette-empty]");
    if (empty) empty.hidden = shown !== 0;
  }

  function openPalette() {
    if (!palette || palette.open) return;
    var input = palette.querySelector("[data-palette-input]");
    if (input) { input.value = ""; }
    filter("");
    // showModal, not show: it traps focus and takes Escape for free, which
    // is the behaviour a hand-rolled overlay spends fifty lines getting
    // wrong.
    palette.showModal();
    if (input) input.focus();
  }

  function move(step) {
    var open = items();
    if (!open.length) return;
    var at = open.indexOf(document.activeElement);
    var next = at === -1
      ? (step > 0 ? 0 : open.length - 1)
      : (at + step + open.length) % open.length;
    open[next].focus();
  }

  // The visible way in. A shortcut with no control behind it is unusable
  // by anyone who has not been told it exists, including on touch.
  Array.prototype.forEach.call(document.querySelectorAll("[data-palette-open]"),
    function (btn) { btn.addEventListener("click", openPalette); });

  if (palette) {
    var input = palette.querySelector("[data-palette-input]");
    if (input) {
      input.addEventListener("input", function () { filter(input.value); });
      input.addEventListener("keydown", function (evt) {
        if (evt.key === "ArrowDown") { evt.preventDefault(); move(1); }
        else if (evt.key === "Enter") {
          // Enter from the box goes to the first match, so the common case
          // is type-three-letters-and-go rather than type-then-arrow.
          var first = items()[0];
          if (first) { evt.preventDefault(); first.click(); }
        }
      });
    }
    palette.addEventListener("keydown", function (evt) {
      if (evt.key === "ArrowDown") { evt.preventDefault(); move(1); }
      else if (evt.key === "ArrowUp") { evt.preventDefault(); move(-1); }
    });
    // Clicking the backdrop is the same gesture as Escape. <dialog> reports
    // it as a click on the dialog itself, outside .c-palette-box.
    palette.addEventListener("click", function (evt) {
      if (evt.target === palette) palette.close();
    });
  }

  document.addEventListener("keydown", function (evt) {
    if ((evt.metaKey || evt.ctrlKey) && evt.key.toLowerCase() === "k") {
      evt.preventDefault();
      openPalette();
      return;
    }
    if (evt.metaKey || evt.ctrlKey || evt.altKey) return;
    if (isTyping(document.activeElement)) return;
    if (palette && palette.open) return;

    /* The dispatcher. Case-insensitive for letters, exact for named keys,
     * and it only ever clicks something already on the page - so the key
     * does precisely what the control beside the hint does, including its
     * confirmation prompt. */
    var key = evt.key;
    var target = document.querySelector('[data-key="' + cssEscape(key) + '"]')
      || (key.length === 1
          ? document.querySelector('[data-key="' + cssEscape(key.toLowerCase()) + '"]')
          : null);
    if (!target) return;
    evt.preventDefault();
    target.click();
  });

  function cssEscape(value) {
    // Only ever a key name, so quoting the handful of characters an
    // attribute selector cares about is enough, and this runs on browsers
    // without CSS.escape.
    return String(value).replace(/["\]/g, "\$&");
  }
})();
