"""Trusted sign-in bootstrap; no bytes or metadata from the protected app."""

import json
from html import escape

from app.core.config import settings
from app.modules.identity.contracts.app_sessions import app_sign_in_url


def render_app_access_page() -> str:
    config = json.dumps(
        {"apiUrl": settings.api_url, "authUrl": app_sign_in_url()}
    ).replace("<", "\\u003c")
    sdk_url = escape(
        settings.api_url.rstrip("/") + "/public/sdk/lemma-client.js", quote=True
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Open app · Lemma</title>
<style>body{{margin:0;background:#f6f5f1;color:#292b26;font:16px system-ui,sans-serif;display:grid;min-height:100vh;place-items:center}}main{{max-width:400px;padding:32px}}h1{{font-size:24px}}a{{color:#305536}}button{{font:inherit;margin-top:16px}}</style></head>
<body><main><h1>Open this app</h1><p id="app-access-status" role="status">Checking your access…</p><a id="app-access-sign-in" hidden>Sign in to Lemma</a><button id="app-access-retry" hidden>Try again</button></main>
<script src="{sdk_url}"></script><script>
window.LemmaClient.startAppAccess({config});
</script></body></html>"""
