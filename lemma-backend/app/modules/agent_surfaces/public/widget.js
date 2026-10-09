/*
 * Lemma web widget: the pod's chat, and forms for its tables, on any page.
 *
 *   <script src="https://API/public/web/widget.js" data-lemma-key="pk_..." async></script>
 *
 * Optional attributes:
 *   data-lemma-token="<host token>"  a signed-in user of the page's product, signed
 *                                    by the page's server with the widget's secret
 *                                    (or call Lemma.identify(token) later). A host
 *                                    token lives ten minutes and so does the chat
 *                                    it opens, unless the page hands over fresh
 *                                    ones: Lemma.identify(function () { return
 *                                    fetch("/my/lemma-token").then(...) })
 *   data-lemma-color="#5a3fd4"       the accent colour
 *   data-lemma-greeting="Hi!"        the first thing the chat says
 *   data-lemma-table="signups"       draw a form for a table the pod opened to
 *                                    visitors, where the script tag is; the chat
 *                                    stays beside it to answer and fill it in
 *   data-lemma-title / -intro / -thanks   the form's words
 *   data-lemma-chat="off"            no chat bubble (on the script, or on a
 *                                    page's own <form data-lemma-table>)
 *
 * A page with its own design marks <form data-lemma-table="signups">, or calls
 * Lemma.addRow("signups", {...}). Either way the table decides which columns
 * may be written; the page only asks. Such a form is never submitted by the
 * browser itself once this script runs; give it method="post" anyway, so a
 * submit before the script arrives keeps the answers out of the URL.
 * data-lemma-page is set by Lemma's hosted page.
 *
 * Everything the server says is built into the page as DOM nodes -- markdown
 * included -- and never parsed as markup.
 *
 * The wire: POST /session trades the session secret kept in localStorage (or a
 * host token, or nothing) for a fifteen-minute access token, which rides as
 * `Authorization: Bearer` on every other call and is never stored. Bodies are
 * JSON. Answers arrive on a fetch stream of one JSON object per line, whose
 * first line is a handshake; polling /history is the fallback, and catches up
 * whatever a stream missed whenever one opens or ends.
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
  var hostToken = script.getAttribute("data-lemma-token") || null;
  var accent = /^#[0-9a-f]{3,8}$/i.test(script.getAttribute("data-lemma-color") || "")
    ? script.getAttribute("data-lemma-color")
    : "#5a3fd4";
  var greeting =
    script.getAttribute("data-lemma-greeting") ||
    (script.getAttribute("data-lemma-table")
      ? "Hi! Ask me anything about this, or tell me your details and I'll fill the form in for you."
      : "Hi! How can we help?");
  var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var pageMode = script.hasAttribute("data-lemma-page");
  var state = { access: null, accessUntil: 0, secret: null, isContact: false, title: "" };

  // Only the session's secret is kept, and only for a visitor nobody signed in:
  // a host session must not outlive the host's own sign-in on a shared machine.
  function remember() {
    try {
      if (state.secret && !hostToken) {
        window.localStorage.setItem(
          storeKey,
          JSON.stringify({ secret: state.secret, isContact: state.isContact, title: state.title })
        );
      } else window.localStorage.removeItem(storeKey);
    } catch (e) {
      /* storage blocked: the chat lasts as long as the page */
    }
  }
  function recalled() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(storeKey) || "null");
      return saved && typeof saved.secret === "string" ? saved : null;
    } catch (e) {
      return null;
    }
  }

  function request(path, options) {
    options = options || {};
    var headers = {};
    if (options.auth !== false && state.access) headers.Authorization = "Bearer " + state.access;
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    return fetch(base + path, {
      method: options.method || (options.body !== undefined ? "POST" : "GET"),
      headers: headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
      credentials: "omit",
    });
  }
  function call(path, options) {
    return request(path, options).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          var error = new Error((data && (data.message || data.error)) || "Something went wrong");
          error.code = data && data.code;
          error.status = response.status;
          throw error;
        }
        return data;
      });
    });
  }

  // The proof-of-work a deployment with bot protection asks for before it starts
  // an anonymous session or sends a code: find the number whose SHA-256 with the
  // salt is the challenge. Nothing to do when the server says it is off.
  function hex(buffer) {
    return Array.prototype.map
      .call(new Uint8Array(buffer), function (b) { return ("0" + b.toString(16)).slice(-2); })
      .join("");
  }
  function solve(challenge) {
    if (!challenge || !challenge.enabled) return Promise.resolve(null);
    if (!window.crypto || !window.crypto.subtle) {
      return Promise.reject(new Error("This page can't complete the security check."));
    }
    var encoder = new TextEncoder();
    function from(number) {
      if (number > challenge.maxnumber) return Promise.reject(new Error("The security check could not be completed."));
      return window.crypto.subtle.digest("SHA-256", encoder.encode(challenge.salt + number)).then(function (digest) {
        if (hex(digest) === challenge.challenge) {
          return btoa(JSON.stringify({
            algorithm: challenge.algorithm, challenge: challenge.challenge, number: number,
            salt: challenge.salt, signature: challenge.signature,
          })).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
        }
        // Yield now and then so a slow machine's page stays usable meanwhile.
        if (number % 500 === 499) return new Promise(function (r) { setTimeout(r, 0); }).then(function () { return from(number + 1); });
        return from(number + 1);
      });
    }
    return from(0);
  }
  function proof(purpose) {
    return call("/challenge?purpose=" + purpose, { auth: false }).then(solve);
  }

  function hostTokenNow() {
    if (typeof hostToken !== "function") return Promise.resolve(hostToken);
    return Promise.resolve(hostToken()).then(function (token) {
      return typeof token === "string" && token ? token : null;
    });
  }
  function adopt(data) {
    state.access = data.access_token;
    state.accessUntil = Date.now() + Math.max(30, (data.expires_in || 900) - 60) * 1000;
    if (data.secret) state.secret = data.secret;
    state.isContact = !!data.is_contact;
    if (data.title) state.title = data.title;
    remember();
    return data;
  }
  // Bumped by Lemma.identify: a session asked for on behalf of whoever was
  // here before is not adopted for whoever is here now.
  var identity = 0;
  function exchange(body) {
    var asked = identity;
    return call("/session", { body: body, auth: false }).then(function (data) {
      return asked === identity ? adopt(data) : ensureSession();
    });
  }
  // A session, kept or new, and a fresh access token for it.
  function start() {
    return hostTokenNow().then(function (token) {
      if (state.secret || token) {
        return exchange({ secret: state.secret, host_token: token }).catch(function (error) {
          if (error.code !== "no_session" || token) throw error;
          state.secret = null;
          remember();
          restartConversation();
          return start();
        });
      }
      return proof("session").then(function (altcha) {
        return exchange({ altcha: altcha });
      });
    });
  }
  var starting = null;
  function ensureSession() {
    if (state.access && Date.now() < state.accessUntil) return Promise.resolve();
    if (!state.secret && !hostToken) {
      var saved = recalled();
      if (saved) {
        state.secret = saved.secret;
        state.isContact = !!saved.isContact;
        state.title = saved.title || "";
      }
    }
    if (!starting) {
      var mine = (starting = start().then(
        function () { if (starting === mine) starting = null; },
        function (error) { if (starting === mine) starting = null; throw error; }
      ));
    }
    return starting;
  }
  // Run ``fn`` with a live access token: refreshed when it has lapsed, and
  // once more if the server says it did anyway.
  function withSession(fn) {
    return ensureSession()
      .then(fn)
      .catch(function (error) {
        if (error.status !== 401 || (error.code !== "bad_token" && error.code !== "no_session")) throw error;
        state.access = null;
        if (error.code === "no_session") {
          state.secret = null;
          restartConversation();
        }
        return ensureSession().then(fn);
      });
  }

  // ------------------------------------------------------------------ styles

  var CSS = [
    ":host{all:initial}",
    ".lw{--a:" + accent + ";--bg:#fff;--sf:#f4f2ef;--ink:#1d1b18;--ink2:#6f6a62;--line:#ebe7e1;",
    "--shadow:0 12px 40px rgba(20,16,10,.16),0 2px 8px rgba(20,16,10,.08);",
    "font:14px/1.5 ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif;color:var(--ink);",
    "-webkit-font-smoothing:antialiased}",
    "@media (prefers-color-scheme:dark){.lw{--bg:#1b1a19;--sf:#292725;--ink:#f2f0ec;--ink2:#a29d95;",
    "--line:#33302d;--shadow:0 12px 40px rgba(0,0,0,.5)}}",
    ".lw *{box-sizing:border-box}",
    ".lw-launch{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border:0;border-radius:50%;",
    "background:var(--a);color:#fff;cursor:pointer;display:grid;place-items:center;box-shadow:var(--shadow);",
    "z-index:2147483646;transition:transform .2s ease}",
    ".lw-launch:hover{transform:scale(1.05)}.lw-launch:active{transform:scale(.96)}",
    ".lw-launch svg{width:26px;height:26px;transition:transform .25s ease,opacity .2s}",
    ".lw-launch .lw-x{position:absolute;opacity:0;transform:rotate(-90deg)}",
    ".lw-open .lw-launch .lw-x{opacity:1;transform:none}.lw-open .lw-launch .lw-bubble{opacity:0;transform:rotate(90deg)}",
    ".lw-dot{position:absolute;top:4px;right:4px;width:12px;height:12px;border-radius:50%;background:#e5484d;",
    "border:2px solid var(--bg);display:none}.lw-unread .lw-dot{display:block}",
    ".lw-panel{position:fixed;right:20px;bottom:88px;width:380px;height:600px;max-height:calc(100vh - 108px);",
    "max-width:calc(100vw - 40px);background:var(--bg);border-radius:18px;box-shadow:var(--shadow);",
    "display:flex;flex-direction:column;overflow:hidden;z-index:2147483647;transform-origin:bottom right;",
    "opacity:0;transform:translateY(12px) scale(.97);visibility:hidden;",
    "transition:opacity .2s ease,transform .25s cubic-bezier(.2,.8,.2,1),visibility 0s .25s}",
    ".lw-open .lw-panel{opacity:1;transform:none;visibility:visible;transition-delay:0s}",
    ".lw-head{display:flex;align-items:center;gap:12px;padding:16px 16px 14px;border-bottom:1px solid var(--line)}",
    ".lw-face{width:36px;height:36px;border-radius:50%;background:var(--a);color:#fff;display:grid;",
    "place-items:center;font-size:15px;font-weight:500;flex:none}",
    ".lw-who{flex:1;min-width:0}.lw-title{font-size:15px;font-weight:500;white-space:nowrap;overflow:hidden;",
    "text-overflow:ellipsis}.lw-sub{font-size:12px;color:var(--ink2)}",
    ".lw-close{border:0;background:none;color:var(--ink2);width:32px;height:32px;border-radius:8px;cursor:pointer;",
    "display:grid;place-items:center}.lw-close:hover{background:var(--sf);color:var(--ink)}",
    ".lw-log{flex:1;overflow-y:auto;padding:18px 16px 8px;display:flex;flex-direction:column;gap:6px;",
    "scroll-behavior:smooth;overscroll-behavior:contain}",
    ".lw-msg{max-width:84%;padding:9px 13px;border-radius:16px;overflow-wrap:anywhere;animation:lw-in .22s ease both}",
    ".lw-user{align-self:flex-end;background:var(--a);color:#fff;border-bottom-right-radius:6px;white-space:pre-wrap}",
    ".lw-assistant{align-self:flex-start;background:var(--sf);border-bottom-left-radius:6px}",
    ".lw-user+.lw-assistant,.lw-assistant+.lw-user{margin-top:8px}",
    ".lw-note{align-self:center;color:var(--ink2);font-size:12px;text-align:center;padding:4px 8px;",
    "animation:lw-in .2s ease both}",
    ".lw-assistant>:first-child{margin-top:0}.lw-assistant>:last-child{margin-bottom:0}",
    ".lw-assistant p,.lw-assistant ul,.lw-assistant ol,.lw-assistant pre,.lw-assistant blockquote{margin:0 0 8px}",
    ".lw-assistant ul,.lw-assistant ol{padding-left:20px}.lw-assistant li{margin:2px 0}",
    ".lw-assistant strong{font-weight:500}.lw-assistant a{color:var(--a);text-underline-offset:2px}",
    ".lw-assistant code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);",
    "padding:1px 5px;border-radius:5px}",
    ".lw-assistant pre{background:var(--bg);padding:10px 12px;border-radius:10px;overflow-x:auto}",
    ".lw-assistant pre code{padding:0;background:none}",
    ".lw-assistant blockquote{border-left:3px solid var(--line);padding-left:10px;color:var(--ink2)}",
    ".lw-assistant .lw-h{font-weight:500;font-size:15px;margin:4px 0 6px}",
    ".lw-assistant hr{border:0;border-top:1px solid var(--line);margin:10px 0}",
    ".lw-assistant .lw-raw{white-space:pre-wrap;margin:0}",
    ".lw-sr{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}",
    ".lw-caret{display:inline-block;width:7px;height:1em;margin-left:2px;vertical-align:-2px;",
    "border-radius:2px;background:var(--a);animation:lw-blink 1s steps(2) infinite}",
    ".lw-typing{display:flex;gap:4px;padding:13px 14px}",
    ".lw-typing i{width:7px;height:7px;border-radius:50%;background:var(--ink2);opacity:.5;",
    "animation:lw-bounce 1.2s infinite ease-in-out}",
    ".lw-typing i:nth-child(2){animation-delay:.15s}.lw-typing i:nth-child(3){animation-delay:.3s}",
    ".lw-foot{padding:10px 12px 12px;border-top:1px solid var(--line)}",
    ".lw-compose{display:flex;align-items:flex-end;gap:8px;background:var(--sf);border-radius:14px;",
    "padding:6px 6px 6px 12px;border:1px solid transparent;transition:border-color .15s}",
    ".lw-compose:focus-within{border-color:var(--a)}",
    ".lw-compose textarea{flex:1;border:0;outline:0;background:none;resize:none;font:inherit;color:inherit;",
    "max-height:120px;padding:6px 0;line-height:1.45}",
    ".lw-compose textarea::placeholder{color:var(--ink2)}",
    ".lw-send{width:34px;height:34px;flex:none;border:0;border-radius:10px;background:var(--a);color:#fff;",
    "cursor:pointer;display:grid;place-items:center;transition:opacity .15s,transform .15s}",
    ".lw-send:disabled{opacity:.35;cursor:default}.lw-send:not(:disabled):active{transform:scale(.92)}",
    ".lw-meta{display:flex;justify-content:space-between;align-items:center;padding:8px 4px 0;font-size:11.5px;",
    "color:var(--ink2)}",
    ".lw-link{border:0;background:none;color:var(--ink2);cursor:pointer;font:inherit;padding:0;",
    "text-decoration:underline;text-underline-offset:2px}.lw-link:hover{color:var(--ink)}",
    ".lw-brand{color:var(--ink2);text-decoration:none}.lw-brand:hover{color:var(--ink)}",
    ".lw-card{background:var(--sf);border-radius:14px;padding:12px;margin-bottom:10px;animation:lw-in .2s ease both}",
    ".lw-card p{margin:0 0 8px;font-size:13px;color:var(--ink2)}",
    ".lw-card form{display:flex;gap:6px}",
    ".lw-card input{flex:1;min-width:0;border:1px solid var(--line);background:var(--bg);color:inherit;",
    "border-radius:10px;padding:8px 10px;font:inherit;outline:0}.lw-card input:focus{border-color:var(--a)}",
    ".lw-card button{border:0;border-radius:10px;padding:8px 12px;background:var(--a);color:#fff;",
    "cursor:pointer;font:inherit}",
    ".lw-inline{position:static!important}.lw-inline .lw-card{margin:12px 0 0}",
    ".lw-page .lw-launch,.lw-page .lw-close{display:none}",
    ".lw-page .lw-panel{position:relative;right:auto;bottom:auto;width:100%;max-width:720px;height:100vh;",
    "max-height:none;margin:0 auto;border-radius:0;opacity:1;visibility:visible;transform:none;box-shadow:none;",
    "border-left:1px solid var(--line);border-right:1px solid var(--line)}",
    ".lw-fc{max-width:560px;margin:0 auto;background:var(--bg);border-radius:18px;padding:28px 28px 24px;",
    "box-shadow:0 1px 2px rgba(20,16,10,.06),0 8px 28px rgba(20,16,10,.08);animation:lw-in .25s ease both}",
    ".lw-pagewrap{padding:48px 16px 0}.lw-inlinewrap .lw-fc{box-shadow:none;border:1px solid var(--line)}",
    ".lw-fc h1{font-size:22px;font-weight:500;margin:0 0 6px;letter-spacing:-.01em}",
    ".lw-eyebrow{font-size:12px;color:var(--ink2);margin:0 0 4px}",
    ".lw-filled input,.lw-filled textarea,.lw-filled select{animation:lw-glow 1.4s ease}",
    "@keyframes lw-glow{0%{box-shadow:0 0 0 3px color-mix(in srgb,var(--a) 35%,transparent);",
    "border-color:var(--a)}100%{box-shadow:none}}",
    ".lw-fc .lw-intro{margin:0 0 20px;color:var(--ink2);white-space:pre-wrap}",
    ".lw-field{display:flex;flex-direction:column;gap:6px;margin-bottom:16px}",
    ".lw-field label{font-weight:500;font-size:13.5px}.lw-field .lw-req{color:var(--ink2);font-weight:400}",
    ".lw-field small{color:var(--ink2);font-size:12px}",
    ".lw-field input,.lw-field textarea,.lw-field select{width:100%;border:1px solid var(--line);background:var(--bg);",
    "color:inherit;border-radius:10px;padding:10px 12px;font:inherit;outline:0;transition:border-color .15s,box-shadow .15s}",
    ".lw-field textarea{min-height:110px;resize:vertical}",
    ".lw-field input:focus,.lw-field textarea:focus,.lw-field select:focus{border-color:var(--a);",
    "box-shadow:0 0 0 3px color-mix(in srgb,var(--a) 18%,transparent)}",
    ".lw-field.lw-bad input,.lw-field.lw-bad textarea,.lw-field.lw-bad select{border-color:#e5484d}",
    ".lw-check{flex-direction:row;align-items:center;gap:10px}.lw-check input{width:18px;height:18px;accent-color:var(--a)}",
    ".lw-err{color:#e5484d;font-size:12.5px;min-height:0}",
    ".lw-submit{width:100%;border:0;border-radius:12px;padding:12px;background:var(--a);color:#fff;font:inherit;",
    "font-weight:500;cursor:pointer;transition:opacity .15s,transform .15s;margin-top:4px}",
    ".lw-submit:disabled{opacity:.6;cursor:default}.lw-submit:not(:disabled):active{transform:scale(.98)}",
    ".lw-done{text-align:center;padding:24px 8px 12px}",
    ".lw-tick{width:56px;height:56px;border-radius:50%;background:var(--a);color:#fff;display:grid;place-items:center;",
    "margin:0 auto 16px;animation:lw-pop .4s cubic-bezier(.2,.9,.3,1.3) both}",
    ".lw-done p{margin:0 0 16px;font-size:15px;color:var(--ink);white-space:pre-wrap}",
    ".lw-fc .lw-card{margin:0 0 16px}",
    "@keyframes lw-pop{from{transform:scale(.4);opacity:0}}",
    "@keyframes lw-in{from{opacity:0;transform:translateY(6px)}}",
    "@keyframes lw-blink{50%{opacity:0}}",
    "@keyframes lw-bounce{0%,80%,100%{transform:translateY(0);opacity:.4}40%{transform:translateY(-4px);opacity:1}}",
    "@media (max-width:480px){.lw-panel{right:0;bottom:0;width:100vw;max-width:none;height:100%;",
    "max-height:none;border-radius:0}.lw-open .lw-launch{display:none}",
    ".lw-pagewrap{padding:0}.lw-pagewrap .lw-fc{border-radius:0;box-shadow:none;min-height:100vh;padding:24px 16px}}",
    "@media (prefers-reduced-motion:reduce){.lw *{animation:none!important;transition:none!important}}",
  ].join("");

  var ICON_CHAT =
    '<svg class="lw-bubble" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';
  var ICON_X =
    '<svg class="lw-x" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  var ICON_SEND =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" ' +
    'stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';

  var host = document.createElement("div");
  var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
  var style = document.createElement("style");
  style.textContent = CSS;
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
  // Only our own constant SVG strings ever go through innerHTML.
  function icon(markup) {
    var holder = document.createElement("span");
    holder.innerHTML = markup;
    return holder.firstChild;
  }

  // ---------------------------------------------------------------- markdown
  // A small renderer for what a model writes: paragraphs, lists, headings,
  // quotes, code, emphasis and links. Every piece of text becomes a text node.

  var INLINE =
    /(`+)([^`]+?)\1|\*\*([^*]+?)\*\*|__([^_]+?)__|\*([^*\s][^*]*?)\*|\[([^\]]+)\]\(([^)\s]+)\)|(https?:\/\/[^\s<]*[^\s<.,;:!?)\]'"])/g;

  // A link only when the model wrote its scheme, and the scheme is the web's or
  // mail's: never javascript: or data:, and never a relative or "//host" link,
  // which would take its meaning from whatever page the chat sits on.
  function safeHref(url) {
    var text = String(url).trim();
    if (!/^(https?|mailto):/i.test(text)) return null;
    try {
      var parsed = new URL(text);
      return /^(https?|mailto):$/.test(parsed.protocol) ? parsed.href : null;
    } catch (e) {
      return null;
    }
  }
  function link(label, url, parent) {
    var href = safeHref(url);
    if (!href) {
      inline(label, parent);
      return;
    }
    var a = el("a");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer nofollow";
    inline(label, a);
    parent.appendChild(a);
  }
  function inline(text, parent) {
    var at = 0, match;
    var pattern = new RegExp(INLINE.source, "g");
    while ((match = pattern.exec(text))) {
      if (match.index > at) parent.appendChild(document.createTextNode(text.slice(at, match.index)));
      if (match[2] != null) parent.appendChild(el("code", null, match[2]));
      else if (match[3] != null || match[4] != null) {
        var strong = el("strong");
        inline(match[3] != null ? match[3] : match[4], strong);
        parent.appendChild(strong);
      } else if (match[5] != null) {
        var em = el("em");
        inline(match[5], em);
        parent.appendChild(em);
      } else if (match[6] != null) link(match[6], match[7], parent);
      else link(match[8], match[8], parent);
      at = pattern.lastIndex;
    }
    if (at < text.length) parent.appendChild(document.createTextNode(text.slice(at)));
  }
  function lines(text, parent) {
    text.split("\n").forEach(function (line, i) {
      if (i) parent.appendChild(el("br"));
      inline(line, parent);
    });
  }

  var LIST_ITEM = /^\s*(?:[-*+]|\d+[.)])\s+/;
  function blockStart(line) {
    return /^```/.test(line) || /^#{1,6}\s/.test(line) || LIST_ITEM.test(line) ||
      /^>\s?/.test(line) || /^\s*(?:-{3,}|\*{3,})\s*$/.test(line);
  }
  function markdown(source, into) {
    into.textContent = "";
    var rows = source.replace(/\r\n?/g, "\n").split("\n");
    var i = 0;
    while (i < rows.length) {
      var row = rows[i];
      if (!row.trim()) { i++; continue; }
      if (/^```/.test(row)) {
        var code = [];
        for (i++; i < rows.length && !/^```/.test(rows[i]); i++) code.push(rows[i]);
        i++;
        var pre = el("pre");
        pre.appendChild(el("code", null, code.join("\n")));
        into.appendChild(pre);
        continue;
      }
      var heading = /^(#{1,6})\s+(.*)$/.exec(row);
      if (heading) {
        var h = el("p", "lw-h");
        inline(heading[2], h);
        into.appendChild(h);
        i++;
        continue;
      }
      if (/^\s*(?:-{3,}|\*{3,})\s*$/.test(row)) { into.appendChild(el("hr")); i++; continue; }
      if (LIST_ITEM.test(row)) {
        var list = el(/^\s*\d/.test(row) ? "ol" : "ul");
        for (; i < rows.length && LIST_ITEM.test(rows[i]); i++) {
          var item = el("li");
          inline(rows[i].replace(LIST_ITEM, ""), item);
          list.appendChild(item);
        }
        into.appendChild(list);
        continue;
      }
      if (/^>\s?/.test(row)) {
        var quote = [];
        for (; i < rows.length && /^>\s?/.test(rows[i]); i++) quote.push(rows[i].replace(/^>\s?/, ""));
        var block = el("blockquote");
        lines(quote.join("\n"), block);
        into.appendChild(block);
        continue;
      }
      var para = [row];
      for (i++; i < rows.length && rows[i].trim() && !blockStart(rows[i]); i++) para.push(rows[i]);
      var p = el("p");
      lines(para.join("\n"), p);
      into.appendChild(p);
    }
  }

  // ------------------------------------------------------------------- chat

  var log, input, sendButton, foot, verifyLink, announcer;
  var launchOpen = function () {};
  var lastSequence = -1, busy = false, typingRow = null, live = null;
  var streaming = false, noStream = !window.ReadableStream || !window.TextDecoder, streamStop = null;
  var poller = null, pollUntil = 0, slowPoller = null, loaded = false;
  // What the visitor sent, drawn at once and confirmed when the server's copy
  // arrives in history -- so it is never drawn twice. Matched by the name the
  // page gave each message, never by its text: the same words twice are two
  // messages, and a message from another tab is not this one.
  var unconfirmed = [];
  var nonces = 0;
  function nonce() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return Date.now().toString(36) + "-" + nonces++ + "-" + Math.random().toString(36).slice(2);
  }

  // Sequence numbers count within one conversation, so a new session -- a
  // different person signed in, or a chat that ended -- starts the log over.
  function restartConversation() {
    // The old conversation's stream says nothing more into the new one's log.
    if (streamStop) streamStop.abort();
    streamStop = null;
    lastSequence = -1;
    unconfirmed = [];
    live = null;
    busy = false;
    loaded = false;
    hideTyping();
    if (!log) return;
    log.textContent = "";
    log.appendChild(el("div", "lw-msg lw-assistant", greeting));
  }

  function isOpen() {
    return shell.classList.contains("lw-open");
  }
  function scrollDown() {
    if (log) log.scrollTop = log.scrollHeight;
  }
  function add(node) {
    if (typingRow && typingRow.parentNode === log) log.insertBefore(node, typingRow);
    else log.appendChild(node);
    scrollDown();
    if (!isOpen()) shell.classList.add("lw-unread");
    return node;
  }
  // Screen readers hear what is finished, once: never a reply mid-sentence.
  function announce(text) {
    if (announcer) announcer.textContent = text;
  }
  function note(text) {
    add(el("div", "lw-note", text));
    announce(text);
  }
  function showTyping() {
    if (typingRow || live) return;
    typingRow = el("div", "lw-msg lw-assistant lw-typing");
    typingRow.setAttribute("aria-label", "Typing");
    typingRow.appendChild(el("i"));
    typingRow.appendChild(el("i"));
    typingRow.appendChild(el("i"));
    log.appendChild(typingRow);
    scrollDown();
  }
  function hideTyping() {
    if (typingRow) typingRow.remove();
    typingRow = null;
  }

  // A reply being written: the text received so far, revealed a little behind
  // it so chunks arriving in bursts read as steady typing. While it is being
  // written each new piece is one more text node; the markdown is built once,
  // when the reply is finished, rather than the whole bubble on every frame.
  function Reply(animate) {
    this.node = add(el("div", "lw-msg lw-assistant"));
    this.raw = this.node.appendChild(el("p", "lw-raw"));
    this.caret = this.raw.appendChild(el("span", "lw-caret"));
    this.target = "";
    this.shown = 0;
    this.painted = 0;
    this.animate = !!animate && !calm;
    this.finished = false;
    this.done = false;
  }
  Reply.prototype.append = function (text) {
    this.target += text;
    this.tick();
  };
  Reply.prototype.reset = function () {
    this.target = "";
    this.shown = this.painted = 0;
    while (this.caret.previousSibling) this.raw.removeChild(this.caret.previousSibling);
  };
  Reply.prototype.tick = function () {
    if (this.done || this.frame) return;
    if (!this.animate) {
      this.shown = this.target.length;
      this.paint();
      return;
    }
    var self = this;
    this.frame = window.requestAnimationFrame(function () {
      self.frame = null;
      var left = self.target.length - self.shown;
      if (left > 0) self.shown += Math.max(2, Math.ceil(left / 10));
      self.paint();
      if (self.shown < self.target.length) self.tick();
    });
  };
  Reply.prototype.paint = function () {
    var nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
    if (this.shown > this.painted) {
      var piece = this.target.slice(this.painted, this.shown);
      this.raw.insertBefore(document.createTextNode(piece), this.caret);
      this.painted = this.shown;
    }
    if (this.finished && this.shown >= this.target.length) {
      this.done = true;
      if (!this.target.trim()) {
        this.node.remove();
        return;
      }
      markdown(this.target, this.node);
      announce(this.node.textContent);
    }
    if (nearBottom) scrollDown();
  };
  /** The whole reply, when the server has it; what was streamed when it does not. */
  Reply.prototype.finish = function (text) {
    this.finished = true;
    if (text == null || text === this.target) this.tick();
    else if (text.indexOf(this.target) === 0) this.append(text.slice(this.target.length));
    else {
      this.reset();
      this.append(text);
    }
  };

  function setBusy(value) {
    busy = value;
    if (busy) showTyping();
    else hideTyping();
  }

  function onFrame(frame) {
    if (frame.type === "typing") {
      setBusy(true);
    } else if (frame.type === "delta") {
      busy = true;
      hideTyping();
      if (!live) live = new Reply(true);
      live.append(frame.text);
    } else if (frame.type === "reset") {
      if (live) live.reset();
    } else if (frame.type === "message") {
      if (typeof frame.sequence === "number") {
        if (frame.sequence <= lastSequence) return;
        lastSequence = frame.sequence;
      }
      hideTyping();
      if (live) live.finish(frame.text);
      else new Reply(true).finish(frame.text);
      live = null;
      if (busy) showTyping();
    } else if (frame.type === "fill") {
      onFill(frame);
    } else if (frame.type === "done") {
      if (live) live.finish();
      live = null;
      setBusy(false);
      window.setTimeout(poll, 400);
    }
  }

  // The stream's frames, one JSON object a line, from pieces that may end
  // anywhere -- mid-line included. A line that is not JSON is skipped.
  function lineSplitter(onFrame) {
    var buffer = "";
    return function (text) {
      buffer += text;
      var parts = buffer.split("\n");
      buffer = parts.pop();
      parts.forEach(function (line) {
        if (!line.trim()) return;
        var frame;
        try {
          frame = JSON.parse(line);
        } catch (e) {
          return; /* a line we do not understand */
        }
        if (frame && typeof frame === "object") onFrame(frame);
      });
    };
  }

  // A stream that will not open is retried with growing, jittered waits, and
  // after a few tries the chat falls back to polling.
  var streamFailures = 0;
  function streamWait() {
    var ceiling = Math.min(30000, 1000 * Math.pow(2, streamFailures));
    return ceiling / 2 + Math.random() * (ceiling / 2);
  }
  function openStream() {
    if (streaming || noStream || !state.access) return;
    streaming = true;
    var opened = false, quiet = false;
    var stop = null;
    withSession(function () {
      // Asked afresh on a retry: a session that ended aborted the last one.
      stop = window.AbortController ? new AbortController() : null;
      streamStop = stop;
      return request("/stream", { signal: stop ? stop.signal : undefined }).then(function (response) {
        if (response.status === 401) {
          return response.json().then(function (data) {
            var error = new Error((data && data.message) || "Sign in again");
            error.code = data && data.code;
            error.status = 401;
            throw error;
          });
        }
        return response;
      });
    })
      .then(function (response) {
        if (response.status === 204) {
          quiet = true;
          return;
        }
        if (!response.ok || !response.body) throw new Error("no stream");
        var reader = response.body.getReader();
        var decoder = new TextDecoder();
        var push = lineSplitter(function (frame) {
          if (stop && stop.signal.aborted) return;
          if (frame.type === "open") {
            // The handshake: the stream is up. Whatever was said while it
            // was down is in the history.
            opened = true;
            streamFailures = 0;
            poll();
            return;
          }
          onFrame(frame);
        });
        function pump() {
          return reader.read().then(function (chunk) {
            if (chunk.done) return;
            push(decoder.decode(chunk.value, { stream: true }));
            return pump();
          });
        }
        return pump();
      })
      .catch(function () {
        if (!opened) streamFailures += 1;
      })
      .then(function () {
        streaming = false;
        if (opened) poll();
        // Nothing has been said yet, so there is nothing to watch: sending a
        // message opens the stream again.
        if (quiet || !busy) return;
        if (streamFailures >= 4) noStream = true;
        if (noStream || !isOpen()) {
          keepPolling(90);
          return;
        }
        window.setTimeout(function () {
          if (busy && isOpen()) openStream();
          else if (busy) keepPolling(90);
        }, opened ? 250 : streamWait());
      });
  }

  function show(message, animate) {
    if (message.sequence <= lastSequence) return;
    lastSequence = message.sequence;
    if (message.role === "user") {
      var mine = message.client_nonce ? unconfirmed.indexOf(message.client_nonce) : -1;
      if (mine >= 0) {
        unconfirmed.splice(mine, 1);
        return;
      }
      add(el("div", "lw-msg lw-user", message.text));
      return;
    }
    if (live) return;
    hideTyping();
    new Reply(animate).finish(message.text);
    if (busy && message.role === "assistant" && !streaming) setBusy(false);
  }
  function poll(initial) {
    if (!state.access || (live && streaming)) return Promise.resolve();
    return withSession(function () {
      return call("/history?after=" + lastSequence);
    })
      .then(function (data) {
        (data.messages || []).forEach(function (m) {
          show(m, !initial);
        });
      })
      .catch(function () {});
  }
  function keepPolling(seconds) {
    pollUntil = Math.max(pollUntil, Date.now() + seconds * 1000);
    if (poller) return;
    poller = window.setInterval(function () {
      poll();
      if (Date.now() > pollUntil || !busy) {
        window.clearInterval(poller);
        poller = null;
      }
    }, 2000);
  }

  function send(text) {
    var id = nonce();
    unconfirmed.push(id);
    var bubble = add(el("div", "lw-msg lw-user", text));
    setBusy(true);
    withSession(function () {
      return call("/messages", { body: { text: text, client_nonce: id } });
    })
      .then(function () {
        if (noStream) keepPolling(120);
        else openStream();
      })
      .catch(function (error) {
        // Not sent: take it back off the log and give the words back, so the
        // visitor can shorten or resend them.
        var at = unconfirmed.indexOf(id);
        if (at >= 0) unconfirmed.splice(at, 1);
        bubble.remove();
        setBusy(false);
        note(error.code === "VALIDATION_ERROR" ? "Keep it under 4000 characters." : error.message);
        if (input && !input.value) {
          input.value = text;
          input.dispatchEvent(new Event("input"));
        }
      });
  }

  // ------------------------------------------------------------ email codes

  var codeCard = null;
  function askForCode(where, after) {
    if (codeCard && codeCard.isConnected) {
      codeCard.querySelector("input").focus();
      return;
    }
    var card = el("div", "lw-card");
    codeCard = card;
    var says = el("p", null, "We'll email you a code, so replies can reach you.");
    var form = el("form");
    var field = el("input");
    field.type = "email";
    field.required = true;
    field.placeholder = "you@example.com";
    field.setAttribute("aria-label", "Your email");
    var go = el("button", null, "Send code");
    go.type = "submit";
    form.appendChild(field);
    form.appendChild(go);
    card.appendChild(says);
    card.appendChild(form);
    where(card);
    field.focus();
    var email = null;
    form.onsubmit = function (event) {
      event.preventDefault();
      var value = field.value.trim();
      if (!value) return;
      go.disabled = true;
      var step = email
        ? withSession(function () {
            return call("/code/verify", { body: { email: email, code: value } });
          }).then(function (data) {
            adopt(data);
            card.remove();
            if (after) after();
          })
        : withSession(function () {
            return proof("code").then(function (altcha) {
              return call("/code", { body: { email: value, altcha: altcha } });
            });
          }).then(function () {
            email = value;
            says.textContent = "Enter the 6-digit code we sent to " + value + ".";
            field.value = "";
            field.type = "text";
            field.inputMode = "numeric";
            field.autocomplete = "one-time-code";
            field.placeholder = "123456";
            field.setAttribute("aria-label", "Code");
            go.textContent = "Confirm";
          });
      step
        .catch(function (error) {
          says.textContent = error.message;
        })
        .then(function () {
          go.disabled = false;
          field.focus();
        });
    };
  }

  // ---------------------------------------------------------------- the chat

  function buildChat() {
    var launch = el("button", "lw-launch");
    launch.setAttribute("aria-label", "Open chat");
    launch.setAttribute("aria-expanded", "false");
    launch.setAttribute("aria-controls", "lw-panel");
    launch.appendChild(icon(ICON_CHAT));
    launch.appendChild(icon(ICON_X));
    launch.appendChild(el("span", "lw-dot"));

    var title = state.title || "Chat";
    var panel = el("div", "lw-panel");
    panel.id = "lw-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", title);
    // A floating chat holds the visitor until it is closed; the hosted page's
    // chat is the page.
    if (!shell.classList.contains("lw-page")) panel.setAttribute("aria-modal", "true");
    var head = el("div", "lw-head");
    head.appendChild(el("div", "lw-face", title.trim().charAt(0).toUpperCase() || "?"));
    var who = el("div", "lw-who");
    who.appendChild(el("div", "lw-title", title));
    who.appendChild(el("div", "lw-sub", "AI assistant · replies right away"));
    head.appendChild(who);
    var close = el("button", "lw-close");
    close.setAttribute("aria-label", "Close chat");
    close.appendChild(icon(ICON_X.replace('class="lw-x" ', 'width="18" height="18" ')));
    head.appendChild(close);

    // Not a live region itself: a reply being written would be read out a
    // word at a time. Finished messages go to the announcer instead.
    log = el("div", "lw-log");
    log.appendChild(el("div", "lw-msg lw-assistant", greeting));
    announcer = el("div", "lw-sr");
    announcer.setAttribute("role", "status");
    announcer.setAttribute("aria-live", "polite");

    foot = el("div", "lw-foot");
    var compose = el("div", "lw-compose");
    input = el("textarea");
    input.rows = 1;
    input.maxLength = 4000;
    input.placeholder = "Write a message…";
    input.setAttribute("aria-label", "Message");
    sendButton = el("button", "lw-send");
    sendButton.setAttribute("aria-label", "Send");
    sendButton.disabled = true;
    sendButton.appendChild(icon(ICON_SEND));
    compose.appendChild(input);
    compose.appendChild(sendButton);
    var meta = el("div", "lw-meta");
    verifyLink = el("button", "lw-link", "Get replies by email");
    var brand = el("a", "lw-brand", "Powered by Lemma");
    brand.href = "https://lemma.work";
    brand.target = "_blank";
    brand.rel = "noopener noreferrer";
    meta.appendChild(state.isContact ? el("span") : verifyLink);
    meta.appendChild(brand);
    foot.appendChild(compose);
    foot.appendChild(meta);

    panel.appendChild(head);
    panel.appendChild(log);
    panel.appendChild(foot);
    panel.appendChild(announcer);
    shell.appendChild(panel);
    shell.appendChild(launch);

    function toggle(open) {
      if (shell.classList.contains("lw-page") && !open) return;
      var wasOpen = isOpen();
      shell.classList.toggle("lw-open", open);
      launch.setAttribute("aria-label", open ? "Close chat" : "Open chat");
      launch.setAttribute("aria-expanded", open ? "true" : "false");
      // One slow poller at most, and none while the chat is closed.
      if (slowPoller) window.clearInterval(slowPoller);
      slowPoller = null;
      if (!open) {
        if (wasOpen) launch.focus();
        return;
      }
      shell.classList.remove("lw-unread");
      input.focus();
      var ready = loaded ? Promise.resolve() : poll(true);
      loaded = true;
      ready.then(function () {
        scrollDown();
        openStream();
      });
      // Follow-ups from the team arrive outside any run, so look now and then.
      slowPoller = window.setInterval(function () {
        if (!busy) poll();
      }, 20000);
    }
    launchOpen = function () {
      toggle(true);
    };
    launch.onclick = function () {
      toggle(!isOpen());
    };
    close.onclick = function () {
      toggle(false);
    };
    shell.addEventListener("keydown", function (event) {
      if (event.key === "Escape") toggle(false);
    });
    verifyLink.onclick = function () {
      verifyLink.style.visibility = "hidden";
      askForCode(
        function (card) {
          foot.insertBefore(card, foot.firstChild);
        },
        function () {
          note("Thanks — we'll reply to your email too.");
        }
      );
    };
    function grow() {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 120) + "px";
      sendButton.disabled = !input.value.trim();
    }
    function submit() {
      var text = input.value.trim();
      if (!text) return;
      input.value = "";
      grow();
      send(text);
    }
    input.addEventListener("input", grow);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        submit();
      }
    });
    sendButton.onclick = submit;
  }

  // ------------------------------------------------------------------- forms
  // A form is a page that adds a row to a table the pod opened to visitors.
  // The table decides which columns may be written and by whom; this only
  // draws the questions and sends the answers. A page can draw its own and
  // call Lemma.addRow instead, or mark a plain <form data-lemma-table="...">.

  var ICON_TICK =
    '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" ' +
    'stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

  /** Fields the chat filled, by table, for every form on the page that asks for them. */
  var fillers = [];

  function spoken(name) {
    var words = String(name).replace(/_/g, " ").trim();
    return words ? words.charAt(0).toUpperCase() + words.slice(1) : name;
  }

  // The table says which control asks for each column (``column.input``), so
  // this form, the members' snippet and any other page draw the same one.
  function fieldInput(column) {
    var kind = column.input || "text";
    var control;
    if (kind === "textarea") {
      control = el("textarea");
    } else if (kind === "select") {
      control = el("select");
      var blank = el("option", null, "Choose…");
      blank.value = "";
      control.appendChild(blank);
      column.options.forEach(function (option) {
        var item = el("option", null, option);
        item.value = option;
        control.appendChild(item);
      });
    } else {
      control = el("input");
      control.type = kind;
      if (kind === "number") control.step = column.type === "INTEGER" ? "1" : "any";
      if (kind === "email" || kind === "tel") control.autocomplete = kind;
    }
    control.name = column.name;
    control.id = "lw-f-" + column.name;
    if (column.required && kind !== "checkbox") control.required = true;
    return control;
  }

  function setValue(control, value) {
    if (control.type === "checkbox") control.checked = value === true || value === "true";
    else control.value = value == null ? "" : String(value);
  }

  function addRow(table, values) {
    return withSession(function () {
      return call("/rows", { body: { table: table, values: values } });
    });
  }

  function describeTable(table) {
    return call("/table?table=" + encodeURIComponent(table), { auth: false });
  }

  // ``session`` settles once the visitor's session has started or failed to:
  // the form is drawn either way, and without one it says why and takes nothing.
  function renderForm(into, spec, session) {
    var card = el("div", "lw-fc");
    into.appendChild(card);
    var heading = el("h1", null, script.getAttribute("data-lemma-title") || spoken(spec.table));
    card.appendChild(heading);
    var intro = script.getAttribute("data-lemma-intro");
    if (intro) card.appendChild(el("p", "lw-intro", intro));
    var form = el("form");
    form.noValidate = true;
    // No prototype, so a column named like one of Object's own keys is just a column.
    var rows = Object.create(null);
    spec.columns.forEach(function (column) {
      var check = column.input === "checkbox";
      var row = el("div", "lw-field" + (check ? " lw-check" : ""));
      var control = fieldInput(column);
      var label = el("label", null, column.description || spoken(column.name));
      label.htmlFor = control.id;
      if (!column.required && !check) label.appendChild(el("span", "lw-req", " (optional)"));
      if (check) {
        row.appendChild(control);
        row.appendChild(label);
      } else {
        row.appendChild(label);
        row.appendChild(control);
      }
      var error = el("div", "lw-err");
      row.appendChild(error);
      rows[column.name] = { row: row, control: control, error: error, column: column };
      form.appendChild(row);
    });
    var problem = el("div", "lw-err");
    var submit = el("button", "lw-submit", "Send");
    submit.type = "submit";
    form.appendChild(problem);
    form.appendChild(submit);
    card.appendChild(form);
    session.then(
      function () {
        if (state.title) card.insertBefore(el("div", "lw-eyebrow", state.title), heading);
      },
      function (error) {
        problem.textContent = (error && error.message) || "This form isn't taking answers right now.";
        submit.disabled = true;
      }
    );
    fillers.push({
      table: spec.table,
      fill: function (values) {
        // Only the table's open columns have a field here, so only they fill.
        Object.keys(values).forEach(function (name) {
          var hit = rows[name];
          if (!hit) return;
          setValue(hit.control, values[name]);
          hit.row.classList.remove("lw-filled");
          void hit.row.offsetWidth;
          hit.row.classList.add("lw-filled");
        });
      },
    });

    function clearErrors() {
      problem.textContent = "";
      Object.keys(rows).forEach(function (name) {
        rows[name].row.classList.remove("lw-bad");
        rows[name].error.textContent = "";
      });
    }
    function mark(message) {
      var named = Object.keys(rows).filter(function (name) {
        var said = spoken(name);
        return message.indexOf(said) === 0 || message.toLowerCase().indexOf(said.toLowerCase()) >= 0;
      })[0];
      var hit = named && rows[named];
      if (!hit) {
        problem.textContent = message;
        return;
      }
      hit.row.classList.add("lw-bad");
      hit.error.textContent = message;
      hit.control.focus();
    }
    function values() {
      var out = {};
      Object.keys(rows).forEach(function (name) {
        var control = rows[name].control;
        out[name] = control.type === "checkbox" ? control.checked : control.value;
      });
      return out;
    }
    function localProblem() {
      var names = Object.keys(rows);
      for (var i = 0; i < names.length; i++) {
        var hit = rows[names[i]];
        var control = hit.control;
        var said = hit.column.description || spoken(hit.column.name);
        if (hit.column.required && (control.type === "checkbox" ? !control.checked : !control.value.trim())) {
          return [hit, said + " is required"];
        }
        if (control.value && control.validity && !control.validity.valid) {
          return [hit, "Check " + said.toLowerCase()];
        }
      }
      return null;
    }
    function done() {
      card.textContent = "";
      var box = el("div", "lw-done");
      var tick = el("div", "lw-tick");
      tick.appendChild(icon(ICON_TICK));
      box.appendChild(tick);
      box.appendChild(el("p", null, script.getAttribute("data-lemma-thanks") || "Thanks, we've got it."));
      var again = el("button", "lw-link", "Send another response");
      again.onclick = function () {
        into.textContent = "";
        fillers = fillers.filter(function (filler) { return filler.table !== spec.table; });
        renderForm(into, spec, session);
      };
      box.appendChild(again);
      card.appendChild(box);
    }
    function deliver() {
      submit.disabled = true;
      submit.textContent = "Sending…";
      return addRow(spec.table, values())
        .then(done)
        .catch(function (error) {
          submit.disabled = false;
          submit.textContent = "Send";
          if (error.code === "needs_contact") {
            state.isContact = false;
            confirmFirst();
            return;
          }
          mark(error.message);
        });
    }
    function confirmFirst() {
      askForCode(function (box) {
        card.insertBefore(box, form);
      }, deliver);
    }
    form.onsubmit = function (event) {
      event.preventDefault();
      clearErrors();
      var local = localProblem();
      if (local) {
        local[0].row.classList.add("lw-bad");
        local[0].error.textContent = local[1];
        local[0].control.focus();
        return;
      }
      if (spec.contacts_only && !state.isContact) {
        confirmFirst();
        return;
      }
      deliver();
    };
  }

  function placeForm(table, session) {
    var wrap = el("div", pageMode ? "lw-pagewrap" : "lw-inlinewrap");
    shell.appendChild(wrap);
    var target = pageMode && document.getElementById("lemma-page");
    if (target) target.appendChild(host);
    else script.parentNode.insertBefore(host, script.nextSibling);
    return describeTable(table).then(function (spec) {
      renderForm(wrap, spec, session);
    }).catch(function (error) {
      wrap.appendChild(el("div", "lw-fc", error.message));
    });
  }

  /** The columns a table opened, asked once a table: what a fill may touch. */
  var openColumns = Object.create(null);
  function columnsOf(table) {
    if (!openColumns[table]) {
      openColumns[table] = describeTable(table).then(function (spec) {
        return spec.columns.map(function (column) { return column.name; });
      });
    }
    return openColumns[table];
  }

  /** A plain <form data-lemma-table="signups"> on the page: what the chat fills. */
  function bindPageForms() {
    var forms = document.querySelectorAll("form[data-lemma-table]");
    Array.prototype.forEach.call(forms, function (form) {
      var table = form.getAttribute("data-lemma-table");
      fillers.push({
        table: table,
        // The page's form may hold fields of its own; only the table's open
        // columns are the chat's to fill.
        fill: function (values) {
          columnsOf(table).then(function (open) {
            open.forEach(function (name) {
              if (!Object.prototype.hasOwnProperty.call(values, name)) return;
              var control = form.elements.namedItem(name);
              if (control && control.nodeName) setValue(control, values[name]);
            });
          }, function () {
            /* the table is closed now: nothing to fill */
          });
        },
      });
    });
  }

  /** Send a page's own form: its fields, as the table's row. */
  function sendPageForm(form) {
    var values = {};
    new FormData(form).forEach(function (value, name) {
      if (typeof value === "string") values[name] = value;
    });
    addRow(form.getAttribute("data-lemma-table"), values)
      .then(function () {
        form.dispatchEvent(new CustomEvent("lemma:added", { bubbles: true }));
        form.reset();
      })
      .catch(function (error) {
        form.dispatchEvent(
          new CustomEvent("lemma:error", { bubbles: true, detail: { code: error.code, message: error.message } })
        );
      });
  }

  // A marked form must never fall back to the browser's own submit, which
  // would send the answers to the page itself. So this listens from the moment
  // the script runs -- before the page has loaded, or any session started.
  document.addEventListener(
    "submit",
    function (event) {
      var form = event.target;
      if (!form || !form.matches || !form.matches("form[data-lemma-table]")) return;
      event.preventDefault();
      sendPageForm(form);
    },
    true
  );

  function onFill(frame) {
    fillers.forEach(function (filler) {
      if (filler.table === frame.table) filler.fill(frame.values || {});
    });
    window.dispatchEvent(new CustomEvent("lemma:fill", { detail: { table: frame.table, values: frame.values } }));
  }

  // What a page of its own can call: an app, or any website with the script.
  window.Lemma = window.LemmaChat = {
    addRow: addRow,
    describeTable: describeTable,
    openChat: function () {
      launchOpen();
    },
    onFill: function (callback) {
      window.addEventListener("lemma:fill", function (event) {
        callback(event.detail);
      });
    },
    /** A host token, or a function returning one (or a promise of one) that is
     *  asked again whenever the chat needs a fresh one. */
    identify: function (token) {
      hostToken = token;
      identity += 1;
      starting = null;
      state.access = null;
      state.secret = null;
      state.isContact = false;
      remember();
      // Another person's conversation, numbered from its own start.
      restartConversation();
      return ensureSession().then(function () {
        if (isOpen()) {
          loaded = true;
          return poll(true);
        }
      });
    },
  };

  function boot() {
    var table = script.getAttribute("data-lemma-table");
    // "off" on the script, or on a page's own form, means no chat bubble.
    var chat =
      script.getAttribute("data-lemma-chat") !== "off" &&
      !document.querySelector('form[data-lemma-table][data-lemma-chat="off"]');
    bindPageForms();
    // Nothing to draw: a page's own forms start a session when one is sent.
    if (!table && !chat) return;
    var session = ensureSession();
    var quiet = function () {
      /* the widget is off, or not allowed on this page */
    };
    session.catch(quiet);
    if (table) {
      placeForm(table, session);
      if (chat) session.then(buildChat, quiet);
      return;
    }
    session.then(function () {
      var target = pageMode && document.getElementById("lemma-page");
      (target || document.body).appendChild(host);
      if (pageMode) shell.classList.add("lw-page");
      buildChat();
      if (pageMode) launchOpen();
    }, quiet);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // The pure pieces, for widget.js's own tests only: a page never sets this.
  if (window.__LEMMA_WIDGET_TEST__) {
    window.__LEMMA_WIDGET_TEST__ = { safeHref: safeHref, markdown: markdown, lineSplitter: lineSplitter };
  }
})();
