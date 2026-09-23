/* The one request htmx structurally cannot make.
 *
 * CloudFront's Origin Access Control signs each request to the Lambda with
 * SigV4, and for a request WITH A BODY the viewer must supply the payload
 * hash itself in x-amz-content-sha256 or the edge rejects it with
 * InvalidSignatureException (ADR-005). htmx cannot add that header: the
 * browser's only SHA-256 is crypto.subtle, which is Promise-only, and every
 * htmx hook that can mutate headers fires synchronously (ADR-007).
 *
 * Every other interaction in the portal carries no body — suspend,
 * reactivate, remove-whitelist and add-whitelist all pass their arguments in
 * the path or query string — so htmx handles them and this file is not
 * involved. What remains is sign-in, whose body is a Cognito ID token and
 * must stay a body: a token in a query string lands in CloudFront access
 * logs, Referer headers and browser history.
 *
 * One request, one file, one caller. It cannot grow.
 */
(function () {
  "use strict";

  function sha256Hex(text) {
    return crypto.subtle
      .digest("SHA-256", new TextEncoder().encode(text))
      .then(function (buf) {
        return Array.prototype.map
          .call(new Uint8Array(buf), function (b) { return b.toString(16).padStart(2, "0"); })
          .join("");
      });
  }

  function show(form, message) {
    // Written into a live region on the EXISTING page. The previous version
    // replaced document.documentElement, which is not a navigation: no
    // page-load event, autofocus does not re-fire, and a live region
    // inserted along with the document is never announced. A screen-reader
    // user submitting a bad token got total silence, on the front door.
    var box = form.querySelector("[data-login-error]");
    if (!box) return;
    box.textContent = message;
    box.hidden = false;
    box.focus();
  }

  function submit(form, evt) {
    evt.preventDefault();
    var button = form.querySelector("button[type=submit]");
    var body = new URLSearchParams(new FormData(form)).toString();

    if (!crypto || !crypto.subtle) {
      // Only exposed on secure origins. Without it the request cannot be
      // signed and would die at the edge with a 403 the application never
      // sees — so say so rather than letting it fail mutely.
      show(form, "This page needs a secure (HTTPS) connection to sign you in.");
      return;
    }

    if (button) { button.disabled = true; button.setAttribute("aria-busy", "true"); }

    sha256Hex(body)
      .then(function (hash) {
        return fetch(form.getAttribute("action"), {
          method: "POST",
          body: body,
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
            "x-amz-content-sha256": hash,
          },
        });
      })
      .then(function (resp) {
        // A successful sign-in redirects; fetch follows it transparently and
        // the Set-Cookie has already applied, so just go where it pointed.
        if (resp.redirected) { window.location = resp.url; return; }
        return resp.text().then(function (text) {
          show(form, text || "Sign-in failed. Please try again.");
        });
      })
      .catch(function () {
        show(form, "We couldn't reach the sign-in service. Check your connection and try again.");
      })
      .finally(function () {
        if (button) { button.disabled = false; button.removeAttribute("aria-busy"); }
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form[data-signed-post]").forEach(function (form) {
      form.addEventListener("submit", function (evt) { submit(form, evt); });
    });
  });
})();
