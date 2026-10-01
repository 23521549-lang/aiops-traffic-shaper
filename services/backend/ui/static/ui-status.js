/* Failure surface, in-flight state, and focus after a swap.
 *
 * htmx fixes half of this for free: its default responseHandling does not
 * swap 4xx/5xx bodies into the page and raises htmx:responseError instead,
 * so CloudFront's XML error document can no longer land inside a table.
 * What htmx does NOT do is tell the user, or decide where focus goes when
 * the element holding it is removed. Both are on us.
 *
 * The defect this replaces: the previous helper reported every transport
 * and HTTP failure to console.error and nothing else. An operator could
 * click Suspend on a tenant, have the request 403, and see the table sit
 * unchanged — indistinguishable from success. That is a security-relevant
 * failure mode, not a cosmetic one.
 */
(function () {
  "use strict";

  var MESSAGES = {
    401: "Your session expired. Reload the page and sign in again — nothing was changed.",
    403: "That request wasn't accepted. Reload the page and try again — nothing was changed.",
    404: "That item no longer exists. Reload the page to see the current state.",
    429: "Too many requests right now. Wait a moment and try again — nothing was changed.",
  };

  function statusBox() { return document.getElementById("ui-status"); }

  function report(message) {
    var box = statusBox();
    if (!box) return;
    box.textContent = message;
    box.hidden = false;
    // role="alert" announces it; the focus move is what gets a keyboard user
    // to it, since the control they used may have just been removed.
    box.focus();
  }

  function clear() {
    var box = statusBox();
    if (box) { box.hidden = true; box.textContent = ""; }
  }

  /* Move a form's values into the query string, leaving no request body.
   *
   * This is what keeps signed-post.js down to one caller. CloudFront's OAC
   * only requires the x-amz-content-sha256 payload hash when a request HAS
   * a body (ADR-005), so a POST whose arguments ride in the URL needs no
   * hashing — and hashing is the thing htmx cannot do, because
   * crypto.subtle is Promise-only and this hook is synchronous.
   *
   * Rearranging already-encoded parameters is pure string work, so it fits
   * a synchronous hook perfectly. Only forms that opt in with
   * data-params-in-url are touched, and only values the user typed travel
   * this way — never a credential. The login token stays a body precisely
   * because a token in a URL lands in access logs, Referer headers and
   * browser history.
   */
  document.body.addEventListener("htmx:configRequest", function (evt) {
    var el = evt.detail.elt;
    if (!el || !el.hasAttribute("data-params-in-url")) return;
    var params = new URLSearchParams();
    Object.keys(evt.detail.parameters).forEach(function (k) {
      var v = evt.detail.parameters[k];
      if (v === null || v === undefined || v === "") return;
      // Several checkboxes under one name arrive as an array. Appending it
      // whole stringifies to "a,b,c", which reaches the server as a single
      // malformed address and reports two skipped rows that were fine.
      if (Array.isArray(v)) {
        v.forEach(function (one) {
          if (one !== null && one !== undefined && one !== "") params.append(k, one);
        });
        return;
      }
      params.append(k, v);
    });
    var qs = params.toString();
    if (qs) evt.detail.path += (evt.detail.path.indexOf("?") === -1 ? "?" : "&") + qs;
    evt.detail.parameters = {};
  });

  document.body.addEventListener("htmx:beforeRequest", function (evt) {
    clear();
    var target = evt.detail.target;
    if (target) target.setAttribute("aria-busy", "true");
  });

  document.body.addEventListener("htmx:afterRequest", function (evt) {
    var target = evt.detail.target;
    if (target) target.removeAttribute("aria-busy");
  });

  document.body.addEventListener("htmx:responseError", function (evt) {
    var status = evt.detail.xhr.status;
    report(MESSAGES[status]
      || ("That didn't go through (error " + status + "). Nothing was changed."));
  });

  document.body.addEventListener("htmx:sendError", function () {
    report("That didn't go through — we couldn't reach the service. Nothing was changed.");
  });

  /* Focus and announcement after a swap.
   *
   * innerHTML on a swap target destroys whatever inside it held focus — the
   * Remove button in the whitelist table, the Suspend button in the tenants
   * table. Focus then falls to <body> and Tab restarts from the top of the
   * document, so removing three entries by keyboard meant tabbing through
   * the whole page three times. Nothing announced the change either.
   *
   * The target carries data-swap-label, and after a swap focus moves to the
   * target itself (tabindex="-1") so the next Tab continues from where the
   * user was working rather than from the start.
   */
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var target = evt.detail.target;
    if (!target) return;

    var live = target.querySelector("[data-live]");
    if (live && live.textContent.trim()) {
      // The server renders a one-sentence summary; announcing that rather
      // than putting aria-live on the table itself avoids re-reading every
      // row on every swap.
      live.setAttribute("role", "status");
    }

    if (document.activeElement === document.body || target.contains(document.activeElement)) {
      if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
      target.focus();
    }
  });
})();
