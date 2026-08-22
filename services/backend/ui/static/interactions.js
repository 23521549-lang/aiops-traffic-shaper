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

      fetch(fetchUrl, { method: method, body: body, headers: headers })
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

  function bindAll(root) {
    root.querySelectorAll("[hx-get],[hx-post],[hx-delete]").forEach(bind);
  }

  document.addEventListener("DOMContentLoaded", function () { bindAll(document); });
})();
