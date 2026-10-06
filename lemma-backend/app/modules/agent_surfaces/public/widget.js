/*
 * Lemma web widget: the pod's chat, and forms for its tables, on any page.
 *
 *   <script src="https://API/public/web/widget.js" data-lemma-key="pk_..." async></script>
 *
 * Optional attributes:
 *   data-lemma-token="<host token>"  a signed-in user of the page's product, signed
 *                                    by the page's server with the widget's secret
 *                                    (or call Lemma.identify(token) later)
 *   data-lemma-color="#5a3fd4"       the accent colour
 *   data-lemma-greeting="Hi!"        the first thing the chat says
 *   data-lemma-table="signups"       draw a form for a table the pod opened to
 *                                    visitors, where the script tag is; the chat
 *                                    stays beside it to answer and fill it in
 *   data-lemma-title / -intro / -thanks   the form's words
 *   data-lemma-chat="off"            no chat bubble
 *
 * A page with its own design marks <form data-lemma-table="signups">, or calls
 * Lemma.addRow("signups", {...}). Either way the table decides which columns
 * may be written; the page only asks. data-lemma-page is set by Lemma's hosted
 * page.
 *
 * Everything the server says is built into the page as DOM nodes -- markdown
 * included -- and never parsed as markup. Requests are JSON sent as text/plain
 * so the browser sends no CORS pre-flight. Answers arrive on a fetch stream of
 * one JSON object per line; polling is the fallback.
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
  var state = { session: null, isContact: false, title: "" };

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

  function request(path, body, signal) {
    return fetch(base + path, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=UTF-8" },
      body: JSON.stringify(body || {}),
      signal: signal,
    });
  }
  function call(path, body) {
    return request(path, body).then(function (response) {
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
      state.title = data.title || "";
      remember(hostToken ? null : { session: state.session, isContact: state.isContact, title: state.title });
      return data;
    });
  }
  function ensureSession() {
    if (state.session) return Promise.resolve();
    var saved = hostToken ? null : recalled();
    if (saved) {
      state.session = saved.session;
      state.isContact = !!saved.isContact;
      state.title = saved.title || "";
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

  function safeHref(url) {
    try {
      var parsed = new URL(url, window.location.href);
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

  var log, input, sendButton, foot, verifyLink;
  var launchOpen = function () {};
  var lastSequence = -1, busy = false, typingRow = null, live = null;
  var streaming = false, noStream = !window.ReadableStream || !window.TextDecoder;
  var poller = null, pollUntil = 0, slowPoller = null;
  // What the visitor sent, drawn at once and confirmed when the server's copy
  // arrives in history -- so it is never drawn twice.
  var unconfirmed = [];

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
  function note(text) {
    add(el("div", "lw-note", text));
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
  // it so chunks arriving in bursts read as steady typing.
  function Reply(text, animate) {
    this.node = add(el("div", "lw-msg lw-assistant"));
    this.target = text || "";
    this.shown = animate && !calm ? 0 : this.target.length;
    this.finished = false;
    this.paint();
    if (this.shown < this.target.length) this.tick();
  }
  Reply.prototype.write = function (text) {
    this.target = text;
    if (this.shown > text.length) this.shown = 0;
    if (calm) this.shown = text.length;
    this.tick();
  };
  Reply.prototype.tick = function () {
    if (this.frame) return;
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
    markdown(this.target.slice(0, this.shown), this.node);
    if (!this.finished || this.shown < this.target.length) {
      var last = this.node.lastElementChild || this.node;
      while (last.lastElementChild && /^(UL|OL|LI|BLOCKQUOTE)$/.test(last.tagName)) last = last.lastElementChild;
      last.appendChild(el("span", "lw-caret"));
    }
    if (nearBottom) scrollDown();
  };
  Reply.prototype.finish = function (text) {
    this.finished = true;
    this.write(text != null ? text : this.target);
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
      if (!live) live = new Reply("", true);
      live.write(live.target + frame.text);
    } else if (frame.type === "reset") {
      if (live) live.write("");
    } else if (frame.type === "message") {
      if (typeof frame.sequence === "number") {
        if (frame.sequence <= lastSequence) return;
        lastSequence = frame.sequence;
      }
      hideTyping();
      if (live) live.finish(frame.text);
      else new Reply(frame.text, true).finish(frame.text);
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

  function openStream() {
    if (streaming || noStream || !state.session) return;
    streaming = true;
    var gotFrames = false;
    request("/stream", { session: state.session })
      .then(function (response) {
        if (response.status === 204) return;
        if (!response.ok || !response.body) throw new Error("no stream");
        var reader = response.body.getReader();
        var decoder = new TextDecoder();
        var buffer = "";
        function pump() {
          return reader.read().then(function (chunk) {
            if (chunk.done) return;
            buffer += decoder.decode(chunk.value, { stream: true });
            var parts = buffer.split("\n");
            buffer = parts.pop();
            parts.forEach(function (line) {
              if (!line.trim()) return;
              gotFrames = true;
              try {
                onFrame(JSON.parse(line));
              } catch (e) {
                /* a line we do not understand */
              }
            });
            return pump();
          });
        }
        return pump();
      })
      .catch(function () {
        if (!gotFrames) noStream = true;
      })
      .then(function () {
        streaming = false;
        if (busy && isOpen() && !noStream) openStream();
        else if (busy) keepPolling(90);
      });
  }

  function show(message, animate) {
    if (message.sequence <= lastSequence) return;
    lastSequence = message.sequence;
    if (message.role === "user") {
      if (unconfirmed.length && unconfirmed[0] === message.text) {
        unconfirmed.shift();
        return;
      }
      add(el("div", "lw-msg lw-user", message.text));
      return;
    }
    if (live) return;
    hideTyping();
    new Reply(message.text, animate).finish(message.text);
    if (busy && message.role === "assistant" && !streaming) setBusy(false);
  }
  function poll(initial) {
    if (!state.session || (live && streaming)) return Promise.resolve();
    return withSession(function () {
      return call("/history", { session: state.session, after: lastSequence });
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
    unconfirmed.push(text);
    add(el("div", "lw-msg lw-user", text));
    setBusy(true);
    withSession(function () {
      return call("/messages", { session: state.session, text: text });
    })
      .then(function () {
        if (noStream) keepPolling(120);
        else openStream();
      })
      .catch(function (error) {
        var at = unconfirmed.indexOf(text);
        if (at >= 0) unconfirmed.splice(at, 1);
        setBusy(false);
        note(error.message);
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
        ? call("/code/verify", { session: state.session, email: email, code: value }).then(function () {
            state.isContact = true;
            if (!hostToken) remember(state);
            card.remove();
            if (after) after();
          })
        : withSession(function () {
            return call("/code", { session: state.session, email: value });
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
    launch.appendChild(icon(ICON_CHAT));
    launch.appendChild(icon(ICON_X));
    launch.appendChild(el("span", "lw-dot"));

    var panel = el("div", "lw-panel");
    panel.setAttribute("role", "dialog");
    var head = el("div", "lw-head");
    var title = state.title || "Chat";
    head.appendChild(el("div", "lw-face", title.trim().charAt(0).toUpperCase() || "?"));
    var who = el("div", "lw-who");
    who.appendChild(el("div", "lw-title", title));
    who.appendChild(el("div", "lw-sub", "AI assistant · replies right away"));
    head.appendChild(who);
    var close = el("button", "lw-close");
    close.setAttribute("aria-label", "Close chat");
    close.appendChild(icon(ICON_X.replace('class="lw-x" ', 'width="18" height="18" ')));
    head.appendChild(close);

    log = el("div", "lw-log");
    log.setAttribute("aria-live", "polite");
    log.appendChild(el("div", "lw-msg lw-assistant", greeting));

    foot = el("div", "lw-foot");
    var compose = el("div", "lw-compose");
    input = el("textarea");
    input.rows = 1;
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
    shell.appendChild(panel);
    shell.appendChild(launch);

    var loaded = false;
    function toggle(open) {
      if (shell.classList.contains("lw-page") && !open) return;
      shell.classList.toggle("lw-open", open);
      launch.setAttribute("aria-label", open ? "Close chat" : "Open chat");
      if (!open) {
        if (slowPoller) window.clearInterval(slowPoller);
        slowPoller = null;
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

  function inputFor(column) {
    switch (column.type) {
      case "INTEGER":
      case "FLOAT":
        return "number";
      case "BOOLEAN":
        return "checkbox";
      case "DATE":
        return "date";
      case "DATETIME":
        return "datetime";
      case "ENUM":
        return "choice";
    }
    var name = column.name.toLowerCase();
    if (name.indexOf("email") >= 0) return "email";
    if (/phone|mobile/.test(name)) return "phone";
    if (/message|note|description|detail|comment/.test(name)) return "long";
    return "text";
  }

  function fieldInput(column) {
    var kind = inputFor(column);
    var control;
    if (kind === "long") {
      control = el("textarea");
    } else if (kind === "choice") {
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
      control.type = {
        email: "email", phone: "tel", number: "number", date: "date",
        datetime: "datetime-local", checkbox: "checkbox",
      }[kind] || "text";
      if (kind === "number") control.step = column.type === "INTEGER" ? "1" : "any";
      if (kind === "email") control.autocomplete = "email";
      if (kind === "phone") control.autocomplete = "tel";
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
      return call("/rows", { session: state.session, table: table, values: values });
    });
  }

  function describeTable(table) {
    return withSession(function () {
      return call("/table", { session: state.session, table: table });
    });
  }

  function renderForm(into, spec) {
    var card = el("div", "lw-fc");
    into.appendChild(card);
    if (state.title) card.appendChild(el("div", "lw-eyebrow", state.title));
    card.appendChild(el("h1", null, script.getAttribute("data-lemma-title") || spoken(spec.table)));
    var intro = script.getAttribute("data-lemma-intro");
    if (intro) card.appendChild(el("p", "lw-intro", intro));
    var form = el("form");
    form.noValidate = true;
    var rows = {};
    spec.columns.forEach(function (column) {
      var check = inputFor(column) === "checkbox";
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
    fillers.push({
      table: spec.table,
      fill: function (values) {
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
        renderForm(into, spec);
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

  function placeForm(table) {
    var wrap = el("div", pageMode ? "lw-pagewrap" : "lw-inlinewrap");
    shell.appendChild(wrap);
    var target = pageMode && document.getElementById("lemma-page");
    if (target) target.appendChild(host);
    else script.parentNode.insertBefore(host, script.nextSibling);
    return describeTable(table).then(function (spec) {
      renderForm(wrap, spec);
    }).catch(function (error) {
      wrap.appendChild(el("div", "lw-fc", error.message));
    });
  }

  /** A plain <form data-lemma-table="signups"> on the page: send its fields. */
  function bindPageForms() {
    var forms = document.querySelectorAll("form[data-lemma-table]");
    Array.prototype.forEach.call(forms, function (form) {
      var table = form.getAttribute("data-lemma-table");
      fillers.push({
        table: table,
        fill: function (values) {
          Object.keys(values).forEach(function (name) {
            var control = form.elements.namedItem(name);
            if (control && control.nodeName) setValue(control, values[name]);
          });
        },
      });
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var values = {};
        new FormData(form).forEach(function (value, name) {
          if (typeof value === "string") values[name] = value;
        });
        addRow(table, values)
          .then(function () {
            form.dispatchEvent(new CustomEvent("lemma:added", { bubbles: true }));
            form.reset();
          })
          .catch(function (error) {
            form.dispatchEvent(
              new CustomEvent("lemma:error", { bubbles: true, detail: { code: error.code, message: error.message } })
            );
          });
      });
    });
  }

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
    identify: function (token) {
      hostToken = token;
      state.session = null;
      return start();
    },
  };

  function boot() {
    ensureSession()
      .then(function () {
        var table = script.getAttribute("data-lemma-table");
        var chat = script.getAttribute("data-lemma-chat") !== "off";
        bindPageForms();
        if (table) {
          placeForm(table);
          if (chat) buildChat();
          return;
        }
        var target = pageMode && document.getElementById("lemma-page");
        (target || document.body).appendChild(host);
        if (pageMode) shell.classList.add("lw-page");
        buildChat();
        if (pageMode) launchOpen();
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
