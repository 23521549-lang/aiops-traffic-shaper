/* Row selection for the console tables.
 *
 * The action itself works without this file. The checkboxes are a real
 * form, the submit button is a real submit, and the server counts what it
 * actually wrote. What this adds is the part a form cannot express: a
 * header checkbox with a correct indeterminate state, a count that is
 * always a ratio, and an action bar that is not on screen when there is
 * nothing for it to act on.
 *
 * "3 selected" is not a sentence an operator can act on. "3 of 19" is: it
 * is the difference between having the whole incident and having a sixth
 * of it, and it is the number they will quote in the ticket.
 */
(function () {
  "use strict";

  function wire(form) {
    var bar = form.querySelector("[data-bulk-bar]");
    var count = form.querySelector("[data-bulk-count]");
    var master = form.querySelector("[data-bulk-master]");
    if (!bar || !count) return;

    function boxes() {
      return Array.prototype.slice.call(
        form.querySelectorAll("input[type=checkbox][data-bulk-row]"));
    }

    function refresh() {
      var all = boxes();
      var picked = all.filter(function (b) { return b.checked; });

      bar.hidden = picked.length === 0;
      count.textContent = picked.length + " of " + all.length + " selected";

      if (master) {
        // Indeterminate is a property, never an attribute: setting it in
        // HTML does nothing at all, which is how these end up rendering as
        // plain unchecked boxes over a partial selection.
        master.checked = picked.length > 0 && picked.length === all.length;
        master.indeterminate = picked.length > 0 && picked.length < all.length;
      }
    }

    if (master) {
      master.addEventListener("change", function () {
        boxes().forEach(function (b) { b.checked = master.checked; });
        refresh();
      });
    }
    form.addEventListener("change", function (evt) {
      if (evt.target && evt.target.hasAttribute("data-bulk-row")) refresh();
    });

    refresh();
  }

  function wireAll() {
    Array.prototype.slice.call(document.querySelectorAll("[data-bulk-form]"))
      .forEach(wire);
  }

  wireAll();
  // A table that arrives through an htmx swap has never been wired, and a
  // stale bar over a fresh table would offer to act on rows that are gone.
  document.body.addEventListener("htmx:afterSwap", wireAll);
})();
