/* Lemma widget kit v1, the acting half: what a widget needs to be an email, an
 * invite or a pull request rather than a picture of one.
 *
 *   lemma.data      the data the widget was displayed with, else its own sample, else null
 *   lemma.isSample  true when lemma.data is the widget's sample, not real data
 *   lemma.act(connector, operation, payload, { kind? }) -> Promise<result>
 *   lemma.button(el, { run, confirm?, yes?, tone?, done?, does? }) -> el
 *   lemma.when(value, { timeOnly?, dateOnly?, ago? }) / lemma.initials(name)
 *   lemma.avatar(name, size?) / lemma.icon(name)  -> HTML
 *
 * `act` runs one connector operation as the person viewing the widget. It finds
 * the organization's install of `connector` by itself -- that lookup is what a
 * widget used to spend its first ten minutes rediscovering -- and `kind`
 * ("http", "composio") names the backend the operation names belong to, so a
 * widget written for one says so plainly on the other instead of failing with a
 * missing operation.
 *
 * `button` is the one way a widget's button calls anything. A `confirm` sentence
 * opens a strip under the button's row that says what will happen and acts only
 * from its own button (`yes` names it), which is what anything leaving Lemma --
 * a send, an approval, a post -- owes the person. The button spins while the
 * call runs and then says what happened, success or error, in the place the
 * person was looking. Over sample data nothing runs: it says what it would do.
 */
(function () {
  var L = window.lemma;
  if (!L || L.act) return;

  var NAMES = {
    gmail: "Gmail", outlook: "Outlook", google_calendar: "Google Calendar", github: "GitHub",
    linear: "Linear", slack: "Slack", intercom: "Intercom", linkedin: "LinkedIn"
  };
  function named(connector) { return NAMES[connector] || connector; }
  function failure(message, code) { var e = new Error(message); e.code = code; return e; }

  var data;
  function read(selector) {
    var tag = document.querySelector(selector);
    if (!tag) return undefined;
    try { return JSON.parse(tag.textContent || "null"); } catch (e) { return undefined; }
  }
  function markSample() {
    var mark = function () {
      if (!document.body || document.querySelector(".lw-sample")) return;
      var note = document.createElement("p");
      note.className = "lw-sample";
      note.textContent = "Sample data. Nothing here is real, and its buttons do nothing.";
      document.body.insertBefore(note, document.body.firstChild);
    };
    if (document.body) mark(); else document.addEventListener("DOMContentLoaded", mark);
  }
  Object.defineProperty(L, "data", {
    get: function () {
      if (data !== undefined) return data;
      var given = read("script[data-lemma-widget-data]");
      if (given !== undefined) { L.isSample = false; return (data = given); }
      var sample = read("script[data-lemma-sample]");
      if (sample !== undefined) { L.isSample = true; markSample(); return (data = sample); }
      return (data = null);
    }
  });
  L.isSample = false;

  var installs = {};
  function install(connector) {
    if (!installs[connector]) {
      var podId = (window.__LEMMA_CONFIG__ || {}).podId;
      installs[connector] = L.client().then(function (c) {
        return c.pods.get(podId).then(function (pod) {
          return c.connectors.authConfigs.list(pod.organization_id, { limit: 200 }).then(function (listed) {
            var found = ((listed && listed.items) || []).filter(function (i) {
              return i.connector_id === connector && String(i.status || "ACTIVE").toUpperCase() === "ACTIVE";
            }).sort(function (a, b) { return (b.is_default ? 1 : 0) - (a.is_default ? 1 : 0); })[0];
            if (!found) throw failure(named(connector) + " isn't connected in this organization yet.", "NOT_INSTALLED");
            return { client: c, organizationId: pod.organization_id, name: found.name || found.id, kind: String(found.kind || "").toLowerCase(), podId: podId };
          });
        });
      });
      installs[connector].catch(function () { delete installs[connector]; });
    }
    return installs[connector];
  }

  function said(error, connector) {
    var code = error && error.code;
    if (code === "ACCOUNT_NOT_FOUND" || code === "POD_ACCOUNT_NOT_FOUND" || code === "ACCOUNT_RESOLUTION_ERROR" || code === "ACCOUNT_CREDENTIALS_NOT_FOUND") {
      return failure("Connect your " + named(connector) + " account in Lemma, then try again.", code);
    }
    if (code === "OPERATION_NOT_FOUND") {
      return failure("This " + named(connector) + " connection can't do that. Ask in the conversation to adapt this widget.", code);
    }
    return error instanceof Error ? error : failure(String(error || "That didn't work."));
  }

  L.act = function (connector, operation, payload, options) {
    return install(connector).then(function (i) {
      var kind = options && options.kind;
      if (kind && i.kind && i.kind !== kind) {
        throw failure("This widget was written for a different kind of " + named(connector) + " connection than this organization has. " +
          "Ask in the conversation to adapt it.", "KIND_MISMATCH");
      }
      return i.client.connectors.operations.execute(
        { organizationId: i.organizationId, authConfigName: i.name, podId: i.podId }, operation, payload || {}
      );
    }).then(function (response) {
      return response && "result" in response ? response.result : response;
    }, function (error) { throw said(error, connector); });
  };

  // A moment, the way a calendar or an inbox says one: "Tue 7 Oct, 14:30".
  L.when = function (value, options) {
    var d = new Date(value);
    if (isNaN(d)) return "—";
    var o = options || {};
    if (o.ago) {
      var mins = Math.round((Date.now() - d) / 60000);
      if (mins >= 0 && mins < 60) return mins <= 1 ? "just now" : mins + "m ago";
      if (mins >= 0 && mins < 1440) return Math.round(mins / 60) + "h ago";
      if (mins >= 0 && mins < 10080) return Math.round(mins / 1440) + "d ago";
    }
    return d.toLocaleString(undefined, o.timeOnly ? { hour: "numeric", minute: "2-digit" }
      : o.dateOnly ? { weekday: "short", day: "numeric", month: "short" }
      : { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  };
  L.initials = function (name) {
    // "Priya Nair <priya@contoso.com>" is initialled from the name before the address.
    var parts = String(name || "?").split("<")[0].trim().split(/[\s@._-]+/).filter(Boolean);
    return ((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
  };
  // A person as a tinted disc: the same name always gets the same hue.
  L.avatar = function (name, size) {
    var h = 0, s = String(name || "?");
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return '<span class="lw-av"' + (size ? ' data-size="' + size + '"' : "") + ' style="--h:' + h + '" title="' + L.esc(s) + '">' + L.esc(L.initials(s)) + "</span>";
  };

  var PATHS = {
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    archive: '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8M10 12h4"/>',
    star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    read: '<path d="M3 9.5 12 4l9 5.5V19a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="m3 9.5 9 6 9-6"/>',
    flag: '<path d="M5 21V4m0 0h11l-2 4 2 4H5"/>',
    send: '<path d="M4 12 20 4l-6 16-2.5-6.5z"/><path d="m11.5 13.5 3-3"/>',
    reply: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 6 6v4"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.01"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    video: '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
    pin: '<path d="M12 21s-7-6.1-7-11.5a7 7 0 0 1 14 0C19 14.9 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    pr: '<circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="18" r="2.2"/><path d="M6 8.2v7.6M18 15.8V10a3 3 0 0 0-3-3h-4m0 0 2.5-2.5M11 7l2.5 2.5"/>',
    merge: '<circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="12" r="2.2"/><path d="M6 8.2v7.6M8 7.2c3 .6 6.5 2.3 7.8 4"/>',
    issue: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="1.6"/>',
    comment: '<path d="M4 5h16v11H9l-5 4z"/>',
    file: '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4"/>',
    branch: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10M18 10c0 4-6 3-12 7"/>',
    hash: '<path d="M5 9h14M4 15h14M10 4 8 20M16 4l-2 16"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    chat: '<path d="M21 12a8 8 0 0 1-11.8 7L4 20l1.1-4.6A8 8 0 1 1 21 12z"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
    dots: '<circle cx="6" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18" cy="12" r="1.2"/>'
  };
  L.icon = function (name) {
    return '<svg class="lw-ico" viewBox="0 0 24 24" aria-hidden="true">' + (PATHS[name] || "") + "</svg>";
  };

  function node(target) { return typeof target === "string" ? document.getElementById(target) : target; }
  // Where a button's confirm strip and result line go: at the end of the nearest
  // [data-say] block (a header whose icon buttons have no room beside them),
  // else under the row the button sits in.
  function place(el, child) {
    var box = el.closest("[data-say]");
    if (box) return box.appendChild(child);
    var at = el.closest(".lw-bar") || el;
    var after = at;
    while (after.nextElementSibling && after.nextElementSibling.classList.contains("lw-confirm")) after = after.nextElementSibling;
    return after.insertAdjacentElement("afterend", child);
  }
  function line(el) {
    var box = el.closest("[data-say]");
    if (box) return box.querySelector(":scope > .lw-said");
    var at = el.closest(".lw-bar") || el;
    for (var n = at.nextElementSibling; n && (n.classList.contains("lw-said") || n.classList.contains("lw-confirm")); n = n.nextElementSibling) {
      if (n.classList.contains("lw-said")) return n;
    }
    return null;
  }
  function note(el, text, kind) {
    var said = line(el);
    if (!said) {
      said = document.createElement("div");
      said.className = "lw-said";
      said.setAttribute("role", "status");
      place(el, said);
    }
    var line_ = said;
    line_.dataset.kind = kind || "";
    line_.innerHTML = text ? (kind === "good" ? L.icon("check") : kind === "bad" ? L.icon("alert") : "") + "<span>" + L.esc(text) + "</span>" : "";
  }

  L.button = function (target, o) {
    var el = node(target);
    var strip = null;
    function close() { if (strip) { strip.remove(); strip = null; } }
    function go() {
      close();
      el.disabled = true;
      el.dataset.busy = "";
      note(el, "");
      Promise.resolve().then(o.run).then(function (result) {
        el.disabled = false;
        delete el.dataset.busy;
        var done = typeof o.done === "function" ? o.done(result) : o.done;
        note(el, done || "Done.", "good");
      }, function (error) {
        el.disabled = false;
        delete el.dataset.busy;
        note(el, (error && error.message) || "That didn't work.", "bad");
      });
    }
    el.addEventListener("click", function () {
      if (el.disabled) return;
      if (L.isSample) { note(el, "Sample data, so nothing was sent. This would " + (o.does || el.textContent.trim().toLowerCase()) + ".", "muted"); return; }
      if (!o.confirm) return go();
      if (strip) return close();
      var text = typeof o.confirm === "function" ? o.confirm() : o.confirm;
      if (!text) return;
      strip = document.createElement("div");
      strip.className = "lw-confirm";
      if (o.tone) strip.dataset.tone = o.tone;
      strip.innerHTML = "<span>" + L.esc(text) + '</span><button class="lw-btn" type="button">Cancel</button>' +
        '<button class="lw-btn" type="button" data-tone="' + (o.tone === "good" ? "good" : o.tone === "danger" ? "danger" : "primary") + '">' + L.esc(o.yes || el.textContent.trim()) + "</button>";
      var buttons = strip.querySelectorAll("button");
      buttons[0].addEventListener("click", close);
      buttons[1].addEventListener("click", go);
      note(el, "");
      place(el, strip);
      buttons[1].focus();
    });
    return el;
  };
})();
