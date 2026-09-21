// A small, custom HTML-attribute-driven AJAX helper for the dashboard and
// control-platform pages. Inspired by htmx's declarative approach, but
// this is NOT the htmx library itself — this offline dev environment has
// no way to fetch and verify the real htmx.js, and reproducing a
// well-tested third-party library from memory risked shipping something
// that looks like htmx but silently isn't. Supports only the attribute
// subset these two pages actually use: hx-get/hx-post/hx-delete,
// hx-target, hx-trigger, hx-confirm. No dependency, no build step.
(function () {
  "use strict";

  // ADR-005. Everything reaches this app through CloudFront, whose origin
  // access control signs the request to the Lambda function URL but NOT the
  // body. Lambda refuses unsigned payloads, so any request WITH a body has to
  // carry the hex SHA-256 of that body or it is rejected at the edge with a
  // 403 the application never sees - and the page would show "interaction
  // failed" for a request the server never heard about.
  //
  // SubtleCrypto is only available in a secure context. That is always true in
  // production (CloudFront is HTTPS-only) and can be false on a plain-http
  // local page, where returning null and sending the request unhashed at least
  // produces the real error rather than a silent no-op.
  function sha256Hex(text) {
    if (!window.crypto || !window.crypto.subtle) return Promise.resolve(null);
    return window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))
      .then(function (buf) {
        var out = "";
        new Uint8Array(buf).forEach(function (b) { out += ("0" + b.toString(16)).slice(-2); });
        return out;
      });
  }

  function withBodyHash(body, headers) {
    if (body === null || body === undefined) return Promise.resolve(headers);
    return sha256Hex(String(body)).then(function (hash) {
      if (hash) headers["x-amz-content-sha256"] = hash;
      return headers;
    });
  }

  function defaultTrigger(el) {
    if (el.tagName === "FORM") return "submit";
    if (el.tagName === "SELECT") return "change";
    return "click";
  }

  function bind(el) {
    var method = el.hasAttribute("hx-get") ? "GET"
      : el.hasAttribute("hx-post") ? "POST"
      : el.hasAttribute("hx-delete") ? "DELETE"
      : null;
    if (!method) return;

    var url = el.getAttribute("hx-get") || el.getAttribute("hx-post") || el.getAttribute("hx-delete");
    var targetSel = el.getAttribute("hx-target");
    var trigger = el.getAttribute("hx-trigger") || defaultTrigger(el);
    var confirmMsg = el.getAttribute("hx-confirm");

    el.addEventListener(trigger, function (evt) {
      if (el.tagName === "FORM" || el.tagName === "A") evt.preventDefault();
      if (confirmMsg && !window.confirm(confirmMsg)) return;

      var fetchUrl = url;
      var body = null;
      var headers = { "X-UI-AJAX": "1" };
      // M7: double-submit — echo the CSRF cookie back in a header. A
      // cross-site form can make the browser send the cookie, but cannot set
      // this header, which is what makes the pair meaningful.
      var csrf = document.cookie.split("; ")
        .filter(function (c) { return c.indexOf("csrf_token=") === 0; })
        .map(function (c) { return c.slice("csrf_token=".length); })[0];
      if (csrf) headers["X-CSRF-Token"] = csrf;

      if (el.tagName === "FORM") {
        var params = new URLSearchParams(new FormData(el));
        if (method === "GET") {
          fetchUrl = url + "?" + params.toString();
        } else {
          body = params;
          headers["Content-Type"] = "application/x-www-form-urlencoded";
        }
      } else if (el.tagName === "SELECT") {
        fetchUrl = url + "?" + encodeURIComponent(el.name) + "=" + encodeURIComponent(el.value);
      }

      withBodyHash(body, headers)
        .then(function (h) { return fetch(fetchUrl, { method: method, body: body, headers: h }); })
        .then(function (resp) {
          var redirect = resp.headers.get("X-UI-Redirect");
          if (redirect) { window.location = redirect; return null; }
          return resp.text();
        })
        .then(function (html) {
          if (html === null) return;
          var target = targetSel ? document.querySelector(targetSel) : el;
          target.innerHTML = html;
          bindAll(target); // newly-swapped-in elements need their own listeners
          if (el.tagName === "FORM") el.reset();
        })
        .catch(function (e) { console.error("interaction failed:", e); });
    });
  }

  // A plain <form method="post"> cannot carry x-amz-content-sha256: the browser
  // builds and sends that request itself. Any form that posts through
  // CloudFront therefore has to go through fetch, which is what
  // data-signed-post marks. The login form is the only one that is not already
  // an hx-post.
  function bindSignedForm(form) {
    form.addEventListener("submit", function (evt) {
      evt.preventDefault();
      var body = new URLSearchParams(new FormData(form)).toString();
      var headers = { "Content-Type": "application/x-www-form-urlencoded" };
      withBodyHash(body, headers)
        .then(function (h) {
          return fetch(form.getAttribute("action"), { method: "POST", body: body, headers: h });
        })
        .then(function (resp) {
          // A successful sign-in redirects; fetch follows it transparently and
          // the Set-Cookie has already applied, so the page just has to go
          // where the server sent it.
          if (resp.redirected) { window.location = resp.url; return null; }
          return resp.text();
        })
        .then(function (html) {
          if (html === null) return;
          // The server re-rendered this page, usually with an error. Replacing
          // the document drops every listener, so rebind before the next
          // attempt turns into an unsigned plain form post.
          document.documentElement.innerHTML = html;
          bindAll(document);
        })
        .catch(function (e) { console.error("sign-in failed:", e); });
    });
  }

  function bindAll(root) {
    root.querySelectorAll("[hx-get],[hx-post],[hx-delete]").forEach(bind);
    root.querySelectorAll("form[data-signed-post]").forEach(bindSignedForm);
  }

  document.addEventListener("DOMContentLoaded", function () { bindAll(document); });
})();
