/* The requests htmx structurally cannot make.
 *
 * CloudFront's Origin Access Control signs each request to the Lambda with
 * SigV4, and for a request WITH A BODY the viewer must supply the payload
 * hash itself in x-amz-content-sha256 or the edge rejects it with
 * InvalidSignatureException (ADR-005). htmx cannot add that header: the
 * browser's only SHA-256 is crypto.subtle, which is Promise-only, and every
 * htmx hook that can mutate headers fires synchronously (ADR-007).
 *
 * Most interactions in the portal carry no body - suspend, reactivate,
 * remove-allowed and add-allowed all pass their arguments in the path or
 * query string - so htmx handles them and this file is not involved.
 *
 * Two do not, for the same reason. Sign-in carries a Cognito ID token, and
 * tenant creation carries another person's email address. Both would land in
 * CloudFront access logs, Referer headers and browser history if they rode in
 * a URL, and spec 12.8 refuses exactly that.
 *
 * It answers a REDIRECT by following it and anything else by printing the
 * response as text, so a caller's failure response has to be plain text. That
 * is the whole contract, and it is what keeps this file from growing into a
 * second htmx: it does not swap fragments and must not learn to.
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
    var box = form.querySelector("[data-post-error]");
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
      show(form, "This page needs a secure (HTTPS) connection to send this.");
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
        // Success redirects; fetch follows it transparently and any
        // Set-Cookie has already applied, so just go where it pointed.
        if (resp.redirected) { window.location = resp.url; return; }
        return resp.text().then(function (text) {
          show(form, text || "That did not go through. Please try again.");
        });
      })
      .catch(function () {
        show(form, "We couldn't reach the server. Check your connection and try again.");
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
