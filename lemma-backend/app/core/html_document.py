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
# and accepts a narrow, presentation-only ``--lemma-widget-*`` theme payload. The
# bridge is kept out of promoted standalone apps, whose starter fallbacks remain
# system-theme aware on their own.
_HEIGHT_BRIDGE = """
    <script data-lemma-embed-bridge>
      (function () {
        var themeTokens = [
          "--lemma-widget-bg", "--lemma-widget-surface", "--lemma-widget-subtle",
          "--lemma-widget-text", "--lemma-widget-muted", "--lemma-widget-border",
          "--lemma-widget-accent", "--lemma-widget-danger",
          "--lemma-widget-danger-soft", "--lemma-widget-radius",
          "--lemma-widget-font", "--lemma-widget-color-scheme",
          "--lemma-widget-chart-1", "--lemma-widget-chart-2",
          "--lemma-widget-chart-3", "--lemma-widget-chart-4",
          "--lemma-widget-chart-5"
        ];
        var post = function () {
          var h = Math.max(
            document.documentElement.scrollHeight || 0,
            document.body ? document.body.scrollHeight : 0,
            240
          );
          parent.postMessage({ type: "lemma-widget-height", height: h }, "*");
        };
        window.addEventListener("message", function (event) {
          if (event.source !== parent || !event.data || event.data.type !== "lemma-widget-theme") return;
          var values = event.data.tokens || {};
          themeTokens.forEach(function (name) {
            var value = values[name];
            if (typeof value === "string" && value.length > 0 && value.length <= 500) {
              document.documentElement.style.setProperty(name, value);
            }
          });
          var theme = event.data.theme;
          if (theme === "light" || theme === "dark") {
            document.documentElement.style.colorScheme = theme;
            document.documentElement.style.setProperty("--lemma-widget-color-scheme", theme);
          }
          post();
        });
        window.addEventListener("load", post);
        try { new ResizeObserver(post).observe(document.documentElement); } catch (e) {}
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
      html, body { min-height: 100%; background: transparent; }
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
