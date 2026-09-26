/* Opening a reason.
 *
 * The approved screen puts the reason AT the row: a held source is a button,
 * and pressing it reveals the seven features with the distance each one
 * contributed. That is the product's whole claim, and it used to live in a
 * side pane that only opened for one row at a time, through a page load.
 *
 * Progressive, like everything else here. Without this file the panels are
 * simply visible - `hidden` is removed by nothing, so the markup ships them
 * closed and a reader with no script sees a longer page rather than a broken
 * one. That is why the toggle is a <button> carrying aria-expanded and not a
 * link to nowhere.
 *
 * Nothing holds a reference across time. With htmx history enabled a restore
 * swap replaces the children of <body>, so a node captured at load is
 * detached and every listener on it dies silently. The handler is delegated
 * from the document for that reason.
 */
(function () {
  "use strict";

  document.addEventListener("click", function (evt) {
    var toggle = evt.target.closest("[data-why-toggle]");
    if (!toggle) return;

    /* The checkbox that selects a row for the bulk action lives INSIDE the
     * panel. Clicking it must not fold the panel away underneath the
     * pointer that just clicked it. */
    if (evt.target.closest("[data-bulk-row], label")) return;

    var panel = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!panel) return;

    var open = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!open));
    panel.hidden = open;
  });

  /* Escape folds whatever is open. Deliberately not advertised in the
   * markup: on load nothing is open. */
  document.addEventListener("keydown", function (evt) {
    if (evt.key !== "Escape") return;
    var t = document.querySelector('[data-why-toggle][aria-expanded="true"]');
    if (t) t.click();
  });
})();
