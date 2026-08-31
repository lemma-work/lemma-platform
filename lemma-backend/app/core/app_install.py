"""Make a hosted app installable to a home screen, and offer it.

An app is already served on an origin of its own
(``<public_slug>.<app_base_domain>``), which is the hard half of being
installable -- a manifest needs a scope, and a subdomain is one. This module is
the other half: the ``<head>`` an entrypoint needs, and the script that decides
when to offer the install and does the offering.

The reserved paths live here rather than in the apps module because both sides
have to agree on them: the injection writes the URLs and
``apps.services.app_install_assets`` answers them. An app owns every other path
on its origin, so they sit under one dotted directory it will not have
authored, alongside the ``/_lemma`` prefix reserved for the app's API door.

What the script will not do is prompt someone the first time they open a link.
The two people who reach an app top-level are its builder and someone following
a share; the builder arrives through the workspace, which marks the URL with
``#install`` so the prompt lands at the moment they asked for a tab, and the
visitor is asked on a second visit, once they have shown they will come back.
"""

from __future__ import annotations

import json

APP_INSTALL_SENTINEL = "data-lemma-app-install"

# One reserved directory for everything the host serves on an app's origin.
RESERVED_ASSET_PREFIX = ".lemma/"

MANIFEST_PATH = f"/{RESERVED_ASSET_PREFIX}manifest.webmanifest"
SERVICE_WORKER_PATH = f"/{RESERVED_ASSET_PREFIX}sw.js"
OFFLINE_PATH = f"/{RESERVED_ASSET_PREFIX}offline.html"
ICON_PATH_TEMPLATE = f"/{RESERVED_ASSET_PREFIX}icon-{{size}}.png"

# The branding badge's plate, so the icon, the install pill and the "Remix on
# Lemma" pill are one visual family. Also the standalone status-bar colour.
PLATE_COLOR = "#141413"

# The workspace's app frame announces itself with this message
# (``lemma-frontend/lib/app/app-theme.ts``). It is how a framed app knows it is
# inside Lemma rather than embedded on an unrelated page, and it carries the
# origin to reply to.
WORKSPACE_HELLO_MESSAGE = "lemma-app-theme"

# What the framed pill asks the workspace to do: open this app top-level with
# the marker below. A sandboxed frame cannot install anything, and a tab it
# opens for itself inherits the sandbox, so the workspace has to do the opening.
INSTALL_REQUEST_MESSAGE = "lemma-app-install-request"

INSTALL_MARKER = "#install"


def install_head_links() -> str:
    """Manifest, icons and theme colour for a public app's entrypoint."""
    return (
        f'<link rel="manifest" href="{MANIFEST_PATH}">'
        f'<link rel="apple-touch-icon" '
        f'href="{ICON_PATH_TEMPLATE.format(size=180)}">'
        f'<link rel="icon" type="image/png" sizes="32x32" '
        f'href="{ICON_PATH_TEMPLATE.format(size=32)}">'
        f'<meta name="theme-color" content="{PLATE_COLOR}">'
        '<meta name="mobile-web-app-capable" content="yes">'
    )


# A closed shadow root on a fixed host element, the same construction as the
# branding badge, so an app's own CSS cannot restyle the offer and the offer
# cannot restyle the app. It takes bottom-left; the badge has bottom-right.
_STYLE = """
:host{all:initial;position:fixed;left:max(12px,env(safe-area-inset-left));
bottom:max(12px,env(safe-area-inset-bottom));z-index:2147483646;
display:inline-flex;align-items:center;gap:6px;
font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.act{box-sizing:border-box;display:inline-flex;height:32px;align-items:center;gap:8px;
padding:0 12px 0 10px;border:1px solid rgba(255,255,255,.16);border-radius:999px;
background:rgba(20,20,19,.94);color:#fff;text-decoration:none;cursor:pointer;
font:500 12px/1 Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
letter-spacing:-.01em;box-shadow:0 8px 28px rgba(0,0,0,.22);
backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
transition:transform 140ms ease,background 140ms ease,box-shadow 140ms ease}
.act:hover:not(:disabled){background:#111;transform:translateY(-1px);
box-shadow:0 10px 32px rgba(0,0,0,.28)}
.act:disabled{cursor:default}
.act:focus-visible{outline:2px solid #8b7af5;outline-offset:3px}
.mark{display:inline-flex;height:16px;align-items:flex-end;gap:2px}
.mark i{display:block;width:3px;border-radius:2px;background:#8b7af5}
.mark i:nth-child(1){height:7px}.mark i:nth-child(2){height:11px}
.mark i:nth-child(3){height:16px}
.x{box-sizing:border-box;flex:0 0 auto;display:inline-flex;width:16px;height:16px;
align-items:center;justify-content:center;padding:0;margin-left:1px;border:0;
border-radius:999px;background:transparent;color:rgba(255,255,255,.5);
font:inherit;cursor:pointer;transition:background 140ms ease,color 140ms ease}
.x:hover{background:rgba(255,255,255,.14);color:#fff}
.x:focus-visible{outline:2px solid #8b7af5;outline-offset:2px}
.x svg{width:9px;height:9px;display:block}
@media(max-width:380px){:host{left:max(8px,env(safe-area-inset-left));
bottom:max(8px,env(safe-area-inset-bottom))}.act{height:30px;padding:0 10px 0 9px}}
@media(prefers-reduced-motion:reduce){.act{transition:none}}
"""

_MARKUP = (
    '<button class="act" type="button">'
    '<span class="mark" aria-hidden="true"><i></i><i></i><i></i></span>'
    '<span class="label"></span></button>'
    '<button class="x" type="button" aria-label="Dismiss">'
    '<svg viewBox="0 0 10 10" fill="none" aria-hidden="true">'
    '<path d="M1 1L9 9M9 1L1 9" stroke="currentColor" stroke-width="1.4" '
    'stroke-linecap="round"/></svg></button>'
)

_SCRIPT = r"""
(function () {
  var SW = %(sw)s;
  var HELLO = %(hello)s;
  var REQUEST = %(request)s;
  var MARKER = %(marker)s;
  var STYLE = %(style)s;
  var MARKUP = %(markup)s;
  // What "came back another time" means. Reloads within one sitting are a
  // single visit -- otherwise refreshing twice reads as intent to return.
  var SESSION_GAP = 6 * 60 * 60 * 1000;

  var store = null;
  try { store = window.localStorage; } catch (e) {}
  var scope = "";
  try { scope = location.host; } catch (e) {}
  var dismissKey = "lemma:app-install:dismissed:" + scope;
  var visitsKey = "lemma:app-install:visits:" + scope;
  var seenKey = "lemma:app-install:seen:" + scope;

  var read = function (key) {
    try { return store ? store.getItem(key) : null; } catch (e) { return null; }
  };
  var write = function (key, value) {
    try { if (store) store.setItem(key, value); } catch (e) {}
  };
  var count = function (key) { return parseInt(read(key) || "0", 10) || 0; };

  if (read(dismissKey) === "1") return;

  var framed = window.top !== window.self;
  var installed = false;
  try {
    installed = window.matchMedia("(display-mode: standalone)").matches
      || window.navigator.standalone === true;
  } catch (e) {}
  if (installed) return;

  var coarse = false;
  try { coarse = window.matchMedia("(pointer: coarse)").matches; } catch (e) {}
  var ua = navigator.userAgent || "";
  var ios = /iP(hone|ad|od)/.test(ua)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  // Safari has no install API at all, so its path is an instruction rather
  // than a button that does something. iOS Chrome is Safari for this purpose.
  var webkit = ios
    || (/Safari/.test(ua) && !/Chrom|Android|CriOS|FxiOS|Edg/.test(ua));

  var deferred = null;
  var workspace = null;
  var host = null;
  var root = null;
  var eligible = false;
  var handedOff = false;

  // The service worker exists to satisfy the browser's installability check,
  // so it is registered only where installing can happen. It caches the
  // offline page and nothing else -- an app gets a new release whenever its
  // author rebuilds, and a cache holding app assets would pin last week's.
  if (!framed && "serviceWorker" in navigator) {
    try {
      navigator.serviceWorker.register(SW, { scope: "/" })["catch"](function () {});
    } catch (e) {}
  }

  if (!framed) {
    var marked = false;
    try { marked = location.hash === MARKER; } catch (e) {}
    if (marked) {
      // The workspace sent them here for exactly this. Take the marker back
      // out of the address bar so it cannot be copied into a shared link.
      eligible = true;
      try {
        history.replaceState(null, "", location.pathname + location.search);
      } catch (e) {}
    } else {
      var now = Date.now();
      var visits = count(visitsKey);
      if (now - count(seenKey) > SESSION_GAP) {
        visits += 1;
        write(visitsKey, String(visits));
        write(seenKey, String(now));
      }
      eligible = visits >= 2;
    }
  }

  function remove() {
    if (host) { host.remove(); host = null; root = null; }
  }

  function dismiss() { write(dismissKey, "1"); remove(); }

  function say(text) {
    if (!root) return;
    root.querySelector(".label").textContent = text;
    root.querySelector(".act").disabled = true;
  }

  function act() {
    if (framed) {
      try { window.parent.postMessage({ type: REQUEST }, workspace); } catch (e) {}
      // Asked once, for this pane. Not the persistent dismissal: the offer
      // still has to appear in the tab the workspace is opening, and if they
      // install there the "appinstalled" handler writes the key for both. But
      // the workspace re-sends its theme on every change, and without this the
      // pill came back in a pane whose owner had already acted on it.
      handedOff = true;
      remove();
      return;
    }
    if (deferred) {
      var prompt = deferred;
      deferred = null;
      remove();
      try {
        prompt.prompt();
        // Accepted or declined, this person has answered. Asking again next
        // visit is how an offer becomes a banner.
        if (prompt.userChoice && prompt.userChoice.then) {
          prompt.userChoice.then(function () { write(dismissKey, "1"); });
        } else {
          write(dismissKey, "1");
        }
      } catch (e) {}
      return;
    }
    say(ios
      ? "Tap Share, then Add to Home Screen"
      : "Open the File menu, then Add to Dock");
  }

  function mount() {
    if (host || !document.body) return;
    host = document.createElement("div");
    host.setAttribute("data-lemma-install-host", "");
    root = host.attachShadow({ mode: "closed" });
    root.innerHTML = "<style>" + STYLE + "</" + "style>" + MARKUP;
    root.querySelector(".label").textContent = coarse
      ? "Add to home screen"
      : "Install app";
    root.querySelector(".act").addEventListener("click", act);
    root.querySelector(".x").addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      dismiss();
    });
    document.body.appendChild(host);
  }

  function offer() {
    if (host || handedOff || read(dismissKey) === "1") return;
    // Framed: only the workspace gets the handoff pill -- an app embedded on
    // someone else's page has no one to hand off to, and the theme handshake
    // is the only thing that tells the two apart. Top-level: something has to
    // be able to carry the install through, either the deferred prompt or
    // Safari's instructions.
    if (framed ? !workspace : !(eligible && (deferred || webkit))) return;
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", mount, { once: true });
    } else {
      mount();
    }
  }

  window.addEventListener("appinstalled", dismiss);
  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferred = event;
    offer();
  });
  if (framed) {
    window.addEventListener("message", function (event) {
      if (event.source !== window.parent) return;
      if (!event.data || event.data.type !== HELLO) return;
      workspace = event.origin;
      offer();
    });
  } else {
    // Safari never fires an install event, so its offer has to start here.
    offer();
  }
})();
"""


def _js(value: str) -> str:
    return json.dumps(value).replace("<", "\\u003c")


def install_prompt_script() -> str:
    """The install offer, as a script element for a public app's entrypoint."""
    body = _SCRIPT % {
        "sw": _js(SERVICE_WORKER_PATH),
        "hello": _js(WORKSPACE_HELLO_MESSAGE),
        "request": _js(INSTALL_REQUEST_MESSAGE),
        "marker": _js(INSTALL_MARKER),
        "style": _js(_STYLE),
        "markup": _js(_MARKUP),
    }
    return f"<script {APP_INSTALL_SENTINEL}>{body}</script>"
