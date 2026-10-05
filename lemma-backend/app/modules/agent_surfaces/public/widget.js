/*
 * Lemma web widget: a chat bubble, or the handler for a form, on any page.
 *
 *   <script src="https://API/public/web/widget.js" data-lemma-key="pk_..." async></script>
 *
 * Optional: data-lemma-token="<host token>" (or LemmaChat.identify(token)) names a
 * signed-in user of the page's product; the page's server signs it with the
 * widget's secret. For a form widget, mark the form: <form data-lemma-form>.
 *
 * Everything the server says is inserted as text, never as markup. Requests
 * are JSON sent as text/plain so the browser sends no CORS pre-flight.
 */
(function () {
  "use strict";
  var script =
    document.currentScript || document.querySelector("script[data-lemma-key]");
  if (!script) return;
  var key = script.getAttribute("data-lemma-key");
  if (!key) return;
  var base = new URL(script.src, window.location.href).origin + "/public/web/" + key;
  var storeKey = "lemma-web:" + key;
  var state = { session: null, isContact: false, kind: "chat", requiresCode: false };
  var hostToken = script.getAttribute("data-lemma-token") || null;

  function remember(saved) {
    try {
      if (saved) window.localStorage.setItem(storeKey, JSON.stringify(saved));
      else window.localStorage.removeItem(storeKey);
    } catch (e) {
      /* storage blocked: the chat lasts as long as the page */
    }
  }
  function recalled() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(storeKey) || "null");
      return saved && typeof saved.session === "string" ? saved : null;
    } catch (e) {
      return null;
    }
  }

  function call(path, body) {
    return fetch(base + path, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=UTF-8" },
      body: JSON.stringify(body || {}),
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          var error = new Error((data && data.error) || "Something went wrong");
          error.code = data && data.code;
          throw error;
        }
        return data;
      });
    });
  }

  function start() {
    return call("/session", { host_token: hostToken }).then(function (data) {
      state.session = data.session;
      state.isContact = data.is_contact;
      state.kind = data.kind;
      state.requiresCode = data.requires_code;
      remember(hostToken ? null : state);
      return data;
    });
  }
  function ensureSession() {
    if (state.session) return Promise.resolve();
    var saved = hostToken ? null : recalled();
    if (saved) {
      state.session = saved.session;
      state.isContact = !!saved.isContact;
      state.kind = saved.kind || "chat";
      state.requiresCode = !!saved.requiresCode;
      return Promise.resolve();
    }
    return start();
  }
  function withSession(fn) {
    return ensureSession()
      .then(fn)
      .catch(function (error) {
        if (error.code !== "no_session") throw error;
        state.session = null;
        remember(null);
        return start().then(fn);
      });
  }

  // ----------------------------------------------------------------- UI shell

  var host = document.createElement("div");
  var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
  var style = document.createElement("style");
  style.textContent =
    ":host{all:initial}" +
    ".lw{font:14px/1.45 system-ui,-apple-system,sans-serif;color:#1d1b18}" +
    ".lw-button{position:fixed;right:20px;bottom:20px;border:0;border-radius:999px;" +
    "padding:12px 18px;background:#5a3fd4;color:#fff;cursor:pointer;" +
    "box-shadow:0 4px 16px rgba(0,0,0,.18);font:inherit;z-index:2147483646}" +
    ".lw-panel{position:fixed;right:20px;bottom:76px;width:340px;max-width:calc(100vw - 32px);" +
    "height:460px;max-height:calc(100vh - 110px);background:#fff;border-radius:14px;" +
    "box-shadow:0 8px 32px rgba(0,0,0,.2);display:none;flex-direction:column;" +
    "overflow:hidden;z-index:2147483647}" +
    ".lw-open .lw-panel{display:flex}" +
    ".lw-log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px}" +
    ".lw-msg{max-width:85%;padding:8px 11px;border-radius:12px;white-space:pre-wrap;" +
    "overflow-wrap:anywhere}" +
    ".lw-user{align-self:flex-end;background:#5a3fd4;color:#fff}" +
    ".lw-assistant{align-self:flex-start;background:#f2efea}" +
    ".lw-note{align-self:center;color:#6f6a62;font-size:12px;text-align:center}" +
    ".lw-row{display:flex;gap:6px;padding:10px;border-top:1px solid #ece8e1}" +
    ".lw-row input{flex:1;border:1px solid #ddd6cc;border-radius:8px;padding:8px;font:inherit}" +
    ".lw-row button{border:0;border-radius:8px;padding:8px 12px;background:#5a3fd4;" +
    "color:#fff;cursor:pointer;font:inherit}" +
    ".lw-link{background:none;border:0;color:#5a3fd4;cursor:pointer;font:inherit;" +
    "font-size:12px;padding:0 14px 10px;text-align:left}";
  root.appendChild(style);
  var shell = document.createElement("div");
  shell.className = "lw";
  root.appendChild(shell);

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  // ------------------------------------------------------------------- chat

  var log, slot, input, lastSequence = -1, poller = null, pollUntil = 0;

  function note(text) {
    log.appendChild(el("div", "lw-msg lw-note", text));
    log.scrollTop = log.scrollHeight;
  }
  // What the visitor sent, drawn at once and confirmed when the server's copy
  // arrives -- so it is never drawn twice.
  var unconfirmed = [];
  function show(message) {
    if (message.sequence <= lastSequence) return;
    lastSequence = message.sequence;
    if (message.role === "user" && unconfirmed.length && unconfirmed[0] === message.text) {
      unconfirmed.shift();
      return;
    }
    log.appendChild(el("div", "lw-msg lw-" + message.role, message.text));
    log.scrollTop = log.scrollHeight;
  }
  function poll() {
    if (!state.session) return;
    withSession(function () {
      return call("/history", { session: state.session, after: lastSequence });
    })
      .then(function (data) {
        (data.messages || []).forEach(show);
      })
      .catch(function () {});
    if (Date.now() > pollUntil && poller) {
      window.clearInterval(poller);
      poller = null;
    }
  }
  function keepPolling(seconds) {
    pollUntil = Math.max(pollUntil, Date.now() + seconds * 1000);
    if (!poller) poller = window.setInterval(poll, 2000);
  }
  function send(text) {
    unconfirmed.push(text);
    log.appendChild(el("div", "lw-msg lw-user", text));
    log.scrollTop = log.scrollHeight;
    withSession(function () {
      return call("/messages", { session: state.session, text: text });
    })
      .then(function () {
        keepPolling(120);
      })
      .catch(function (error) {
        var at = unconfirmed.indexOf(text);
        if (at >= 0) unconfirmed.splice(at, 1);
        note(error.message);
      });
  }

  var codeRow = null;
  function askForCode(after) {
    if (codeRow && codeRow.isConnected) {
      codeRow.querySelector("input").focus();
      return;
    }
    var row = el("div", "lw-row");
    codeRow = row;
    var field = el("input");
    field.type = "email";
    field.placeholder = "Your email";
    var go = el("button", null, "Send code");
    row.appendChild(field);
    row.appendChild(go);
    (slot || shell).appendChild(row);
    go.onclick = function () {
      var email = field.value.trim();
      withSession(function () {
        return call("/code", { session: state.session, email: email });
      })
        .then(function () {
          field.value = "";
          field.type = "text";
          field.placeholder = "6-digit code";
          go.textContent = "Confirm";
          go.onclick = function () {
            call("/code/verify", {
              session: state.session,
              email: email,
              code: field.value.trim(),
            })
              .then(function () {
                state.isContact = true;
                if (!hostToken) remember(state);
                row.remove();
                if (after) after();
              })
              .catch(function (error) {
                field.value = "";
                field.placeholder = error.message;
              });
          };
        })
        .catch(function (error) {
          field.placeholder = error.message;
          field.value = "";
        });
    };
  }

  function buildChat() {
    var button = el("button", "lw-button", "Chat with us");
    var panel = el("div", "lw-panel");
    log = el("div", "lw-log");
    var row = el("div", "lw-row");
    input = el("input");
    input.placeholder = "Write a message";
    var sendButton = el("button", null, "Send");
    var verify = el("button", "lw-link", "Get replies by email");
    row.appendChild(input);
    row.appendChild(sendButton);
    panel.appendChild(log);
    slot = el("div");
    panel.appendChild(slot);
    panel.appendChild(verify);
    panel.appendChild(row);
    shell.appendChild(panel);
    shell.appendChild(button);
    button.onclick = function () {
      var opening = !shell.classList.contains("lw-open");
      shell.classList.toggle("lw-open");
      if (opening) {
        if (state.isContact) verify.style.display = "none";
        poll();
        keepPolling(30);
        input.focus();
      }
    };
    verify.onclick = function () {
      verify.style.display = "none";
      askForCode(function () {
        note("Thanks, we'll know it's you.");
      });
    };
    function submit() {
      var text = input.value.trim();
      if (!text) return;
      input.value = "";
      send(text);
    }
    sendButton.onclick = submit;
    input.onkeydown = function (event) {
      if (event.key === "Enter") submit();
    };
  }

  // ------------------------------------------------------------------- forms

  function bindForms() {
    var forms = document.querySelectorAll("form[data-lemma-form]");
    Array.prototype.forEach.call(forms, function (form) {
      var target = form.getAttribute("data-lemma-form");
      if (target && target !== key) return;
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var values = {};
        new FormData(form).forEach(function (value, name) {
          if (typeof value === "string") values[name] = value;
        });
        function deliver() {
          return call("/submit", { session: state.session, input: values })
            .then(function (data) {
              form.dispatchEvent(
                new CustomEvent("lemma:submitted", { detail: data.result })
              );
              form.reset();
            })
            .catch(function (error) {
              form.dispatchEvent(
                new CustomEvent("lemma:error", { detail: error.message })
              );
            });
        }
        ensureSession().then(function () {
          if (state.requiresCode && !state.isContact) {
            slot = null;
            shell.style.cssText = "";
            form.parentNode.insertBefore(host, form.nextSibling);
            askForCode(deliver);
          } else {
            deliver();
          }
        });
      });
    });
  }

  window.LemmaChat = {
    identify: function (token) {
      hostToken = token;
      state.session = null;
      return start();
    },
  };

  function boot() {
    ensureSession()
      .then(function () {
        if (state.kind === "form") {
          bindForms();
          return;
        }
        document.body.appendChild(host);
        buildChat();
      })
      .catch(function () {
        /* the widget is off, or not allowed on this page */
      });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
