"""Wrap an HTML fragment into a full standalone document.

Conversation widgets are authored as HTML *fragments* (no doctype/html/head/
body — the display_resource tool enforces this). To serve a widget the same way a
app is served — a full page the browser SDK can run in — the fragment is
wrapped into a complete document here. Promotion preserves the source fragment but
uses the standalone wrapper, which intentionally omits conversation-only padding and
height messaging.

Pod context (window.__LEMMA_CONFIG__) is injected separately by
app.core.runtime_config; this module only builds the document shell.
"""

from __future__ import annotations

import re

# Content that already declares a full document is served as-is (only config is
# injected). Mirrors the frontend's normalizeWidgetContent check.
_FULL_DOC_RE = re.compile(r"<!doctype|<html[\s>]|<body[\s>]", re.IGNORECASE)

# Keeps an in-conversation iframe integrated with its host: reports rendered height
# and applies the host's ``--lemma-widget-*`` theme payload. The bridge is kept out
# of promoted standalone apps, whose fallbacks remain system-theme aware on their own.
#
# The theme is taken by PREFIX rather than from a list of names. A curated list was
# a guess at what a widget would ever want to draw, and it guessed low: the host
# frontends send state colours, the ink for an accent fill, soft accents and the
# corner scale, and every one of them was dropped on the floor here. So a widget
# could not paint a green "done" or an amber "waiting" and had to reach for the
# accent, which is why every status in every widget came out the same colour. What
# is left is the part that is about soundness rather than taste: this is a style
# sink, so a value carrying CSS syntax is refused, and the payload is bounded.
_HEIGHT_BRIDGE = """
    <script data-lemma-embed-bridge>
      (function () {
        var TOKEN = /^--lemma-widget-[a-z0-9-]+$/;
        // A value goes straight into a style declaration, so anything that could
        // close it and start another is not a colour. `event.source !== parent`
        // already establishes the sender is the host; this is the seatbelt.
        var UNSAFE = /[;{}<>]|url\\(|@import|expression\\(/i;
        var MAX_TOKENS = 100;
        var last = -1;
        var post = function () {
          var body = document.body;
          if (!body) return;
          // The BODY, never `documentElement.scrollHeight`. That one reports the
          // scrolling area, which the spec clamps to the viewport — so it hands
          // back whatever height the host just gave the frame, and the widget
          // ratchets upward and can never shrink. Removing `min-height` was read
          // as a fix for this and was not: the clamp is in scrollHeight itself.
          var h = Math.ceil(Math.max(
            body.getBoundingClientRect().height || 0,
            body.scrollHeight || 0
          ));
          // Nothing is learned by re-sending the same number, and a zero before
          // first layout would collapse the frame.
          if (h <= 0 || h === last) return;
          last = h;
          parent.postMessage({ type: "lemma-widget-height", height: h }, "*");
        };
        window.addEventListener("message", function (event) {
          if (event.source !== parent || !event.data || event.data.type !== "lemma-widget-theme") return;
          var values = event.data.tokens || {};
          var applied = 0;
          Object.keys(values).forEach(function (name) {
            if (applied >= MAX_TOKENS || !TOKEN.test(name)) return;
            var value = values[name];
            if (typeof value !== "string" || !value.length || value.length > 512) return;
            if (UNSAFE.test(value)) return;
            document.documentElement.style.setProperty(name, value);
            applied++;
          });
          var theme = event.data.theme;
          if (theme === "light" || theme === "dark") {
            document.documentElement.style.colorScheme = theme;
            document.documentElement.style.setProperty("--lemma-widget-color-scheme", theme);
          }
          post();
        });
        window.addEventListener("load", post);
        try { new ResizeObserver(post).observe(document.body); } catch (e) {}
        post();
      })();
    </script>"""

# Minimal, non-opinionated reset only — the widget/app owns its own typography,
# colors, and layout (so it looks intentional standalone and stays portable).
_RESET_STYLES = """
      *, *::before, *::after { box-sizing: border-box; }
      html, body { margin: 0; }
      img, svg, canvas, video { max-width: 100%; }
      button, input, select, textarea { font: inherit; }"""

# Embedding chrome: only applied to the in-conversation iframe so the widget
# blends into the conversation surface. A standalone (promoted) app gets none of
# this — it owns the full page.
_EMBED_STYLES = """
      html, body { background: transparent; }
      body { padding: 16px; }"""


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def wrap_html_fragment(content: str, *, title: str = "", embed: bool = True) -> str:
    """Wrap a fragment into a full HTML document.

    - ``embed=True`` (in-conversation widget): transparent page + a height bridge
      and theme bridge so the iframe can auto-size and follow the explicit host theme.
    - ``embed=False`` (standalone / promoted app): same shell, no height bridge.

    Content that already declares ``<!doctype>``/``<html>``/``<body>`` is returned
    unchanged — it is already a full document.
    """
    fragment = (content or "").strip()
    if _FULL_DOC_RE.search(fragment):
        return fragment

    bridge = _HEIGHT_BRIDGE if embed else ""
    styles = _RESET_STYLES + (_EMBED_STYLES if embed else "")
    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{_escape(title)}</title>
    <style>{styles}
    </style>
  </head>
  <body>
    {fragment}{bridge}
  </body>
</html>"""
