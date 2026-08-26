"""Browser runtime-config injection for served HTML (apps and widgets).

An app and a conversation widget are the same primitive — a pod-authenticated
HTML page that reads ``window.__LEMMA_CONFIG__`` and talks to the pod through the
browser SDK. The host injects that config at serve time so the artifact bakes in
nothing and is portable between contexts (a widget's source fragment can be promoted
to an app without modification). This module is the shared kernel both serving paths
use; it operates on a ``pod_id``, not on any app/widget entity.
"""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import PurePosixPath
from urllib.parse import urlencode
from uuid import UUID

from app.core.config import settings

# Sentinel attribute marking the injected <script>. Idempotency keys off this,
# NOT the bare global name — a page that merely *reads* window.__LEMMA_CONFIG__
# must still receive injection.
RUNTIME_CONFIG_SENTINEL = "data-lemma-runtime-config"
SOCIAL_METADATA_SENTINEL = "data-lemma-social-metadata"
APP_BRANDING_SENTINEL = "data-lemma-app-branding"

# Where an app reaches the API: its own origin, under a reserved prefix that
# ``AppHostRoutingMiddleware`` strips back off.
#
# Relative on purpose. The SDK resolves a non-absolute ``apiUrl`` against
# ``window.location.origin`` (``resolveApiBase`` in the browser SDK's
# supertokens module), so the app's calls are same-origin wherever it is served
# and nothing here needs to know the request's Host. Same-origin is the point:
# a browser only sends the session cookie first-party, and on desktop every
# `.localhost` host is a separate site as far as WebKit is concerned, so an app
# calling the API's real host got no cookie and loaded signed out.
APP_ORIGIN_API_URL = "/_lemma"


def build_runtime_app_identity(
    name: str,
    description: str | None = None,
    public_url: str | None = None,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "name": name,
            "description": description,
            "url": public_url,
        }.items()
        if value
    }


def is_runtime_config_entrypoint(asset_path: str) -> bool:
    return asset_path in {"", "index.html"} or bool(
        asset_path and "." not in PurePosixPath(asset_path).name
    )


def build_runtime_config(
    pod_id: UUID | str,
    *,
    app: dict[str, str] | None = None,
    app_id: UUID | str | None = None,
    api_url: str | None = None,
) -> dict[str, object]:
    """Pod context handed to the browser SDK at serve time.

    No-build pages bake nothing in; the SDK's resolveConfig prefers this
    ``window.__LEMMA_CONFIG__`` global over env, so the host is the single source
    of truth for which pod/api/auth a served page talks to.

    ``api_url`` overrides the API the page talks to. Pages served on an app's
    own origin pass ``APP_ORIGIN_API_URL`` so their calls stay first-party and
    carry the session cookie; widgets are served from the API host itself and
    take the default.
    """
    config: dict[str, object] = {
        "podId": str(pod_id),
        "apiUrl": api_url or settings.api_url,
        "authUrl": settings.auth_frontend_url,
    }
    if app_id:
        # Two things at once: the app names itself so its API calls resolve to
        # the APP origin rather than a generic SDK caller, and it says *which*
        # app, which is what makes a per-app session countable. Both travel as
        # request headers from the SDK; neither is readable from the server side
        # of an asset request, which is unauthenticated by design.
        config["appId"] = str(app_id)
        config["client"] = "lemma-app"
    if app:
        config["app"] = {
            key: value
            for key, value in app.items()
            if key in {"name", "description", "iconUrl", "url"} and value
        }
    return config


def _public_app_social_metadata(app: dict[str, str] | None) -> str:
    if not app or not app.get("url"):
        return ""
    name = app.get("name") or "A Lemma app"
    description = app.get("description") or f"Run {name} on Lemma."
    public_url = app["url"]
    image_query = urlencode(
        {
            "variant": "run",
            "title": name,
            "detail": description,
            "label": public_url.removeprefix("https://").removeprefix("http://"),
        }
    )
    image_url = f"https://lemma.work/api/social-card?{image_query}"
    escaped_name = html.escape(name, quote=True)
    escaped_description = html.escape(description, quote=True)
    escaped_url = html.escape(public_url, quote=True)
    escaped_image = html.escape(image_url, quote=True)
    return (
        f"<meta {SOCIAL_METADATA_SENTINEL}>"
        f'<meta property="og:title" content="{escaped_name}">'
        f'<meta property="og:description" content="{escaped_description}">'
        '<meta property="og:type" content="website">'
        f'<meta property="og:url" content="{escaped_url}">'
        f'<meta property="og:image" content="{escaped_image}">'
        '<meta name="twitter:card" content="summary_large_image">'
        f'<meta name="twitter:title" content="{escaped_name}">'
        f'<meta name="twitter:description" content="{escaped_description}">'
        f'<meta name="twitter:image" content="{escaped_image}">'
        f'<link rel="canonical" href="{escaped_url}">'
    )


def build_app_branding(public_url: str) -> dict[str, str]:
    """Build the host-controlled attribution shown on a public app."""

    remix_query = urlencode(
        {
            "source": public_url,
            "utm_source": "public_app",
            "utm_medium": "badge",
            "utm_campaign": "remix",
        }
    )
    remix_url = f"{settings.frontend_url.rstrip('/')}/remix?{remix_query}"
    return {
        "label": "Remix on Lemma",
        "url": remix_url,
    }


def _public_app_branding_script(branding: dict[str, str] | None) -> str:
    if not branding or not branding.get("url"):
        return ""

    payload = json.dumps(
        {
            "label": branding.get("label") or "Remix on Lemma",
            "url": branding["url"],
        }
    ).replace("<", "\\u003c")
    return (
        f"<script {APP_BRANDING_SENTINEL}>"
        "(function(){"
        f"const config={payload};"
        "let dismissKey='lemma:app-branding:dismissed';"
        "try{dismissKey+=':'+location.host;"
        "if(localStorage.getItem(dismissKey)==='1')return;}catch(e){}"
        "const mount=function(){"
        "if(!document.body||document.querySelector('[data-lemma-branding-host]'))return;"
        "const host=document.createElement('div');"
        "host.setAttribute('data-lemma-branding-host','');"
        "const root=host.attachShadow({mode:'closed'});"
        "root.innerHTML="
        "'<style>"
        ":host{all:initial;position:fixed;right:max(12px,env(safe-area-inset-right));"
        "bottom:max(12px,env(safe-area-inset-bottom));z-index:2147483647;"
        "display:inline-flex;align-items:center;gap:6px;"
        'font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}'
        "a{box-sizing:border-box;display:inline-flex;height:32px;align-items:center;gap:8px;"
        "padding:0 12px 0 10px;border:1px solid rgba(255,255,255,.16);border-radius:999px;"
        "background:rgba(20,20,19,.94);color:#fff;text-decoration:none;"
        'font:600 12px/1 Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
        "letter-spacing:-.01em;box-shadow:0 8px 28px rgba(0,0,0,.22);"
        "backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);"
        "transition:transform 140ms ease,background 140ms ease,box-shadow 140ms ease}"
        "a:hover{background:#111;transform:translateY(-1px);box-shadow:0 10px 32px rgba(0,0,0,.28)}"
        # The badge always renders on its own near-black pill, so the mark and
        # the focus ring take the dark-stock violet (#8b7af5) rather than the
        # light one — same rule the app's .dark theme follows.
        "a:focus-visible{outline:2px solid #8b7af5;outline-offset:3px}"
        ".mark{display:inline-flex;height:16px;align-items:flex-end;gap:2px}"
        ".mark i{display:block;width:3px;border-radius:2px;background:#8b7af5}"
        ".mark i:nth-child(1){height:7px}.mark i:nth-child(2){height:11px}"
        ".mark i:nth-child(3){height:16px}"
        "button{box-sizing:border-box;flex:0 0 auto;display:inline-flex;width:16px;"
        "height:16px;align-items:center;justify-content:center;padding:0;margin-left:1px;"
        "border:0;border-radius:999px;background:transparent;color:rgba(255,255,255,.5);"
        "font:inherit;cursor:pointer;transition:background 140ms ease,color 140ms ease}"
        "button:hover{background:rgba(255,255,255,.14);color:#fff}"
        "button:focus-visible{outline:2px solid #8b7af5;outline-offset:2px}"
        "button svg{width:9px;height:9px;display:block}"
        "@media(max-width:380px){:host{right:max(8px,env(safe-area-inset-right));"
        "bottom:max(8px,env(safe-area-inset-bottom))}a{height:30px;padding:0 10px 0 9px}}"
        "@media(prefers-reduced-motion:reduce){a{transition:none}}"
        "</style>"
        '<a target="_blank" rel="noopener noreferrer">'
        '<span class="mark" aria-hidden="true"><i></i><i></i><i></i></span>'
        '<span class="label"></span></a>'
        '<button type="button" aria-label="Dismiss">'
        '<svg viewBox="0 0 10 10" fill="none" aria-hidden="true">'
        '<path d="M1 1L9 9M9 1L1 9" stroke="currentColor" stroke-width="1.4" '
        'stroke-linecap="round"/></svg></button>\';'
        "const link=root.querySelector('a');"
        "link.href=config.url;"
        "link.setAttribute('aria-label',config.label);"
        "root.querySelector('.label').textContent=config.label;"
        "root.querySelector('button').addEventListener('click',function(ev){"
        "ev.preventDefault();ev.stopPropagation();"
        "try{localStorage.setItem(dismissKey,'1');}catch(e){}"
        "host.remove();"
        "});"
        "document.body.appendChild(host);"
        "};"
        "if(document.readyState==='loading'){"
        "document.addEventListener('DOMContentLoaded',mount,{once:true});"
        "}else{mount();}"
        "})();"
        "</script>"
    )


def runtime_config_token(
    pod_id: UUID | str,
    *,
    app: dict[str, str] | None = None,
    branding: dict[str, str] | None = None,
    api_url: str | None = None,
) -> str:
    """Short, stable hash of the runtime config, for cache busting (ETags)."""
    config = build_runtime_config(pod_id, app=app, api_url=api_url)
    token_payload: object = (
        {"config": config, "branding": branding} if branding else config
    )
    payload = json.dumps(token_payload, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def inject_runtime_config(
    content: bytes | str,
    pod_id: UUID | str,
    *,
    app: dict[str, str] | None = None,
    app_id: UUID | str | None = None,
    branding: dict[str, str] | None = None,
    api_url: str | None = None,
) -> bytes:
    """Insert host runtime data and presentation into an HTML entrypoint.

    Idempotent via the ``data-lemma-runtime-config`` sentinel attribute. Config
    values are JSON-encoded and ``<``-escaped so they cannot break out of the
    script element. Non-text content is returned unchanged.
    """
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return content
    else:
        text = content

    injection = ""
    if RUNTIME_CONFIG_SENTINEL not in text:
        payload = json.dumps(
            build_runtime_config(pod_id, app=app, app_id=app_id, api_url=api_url)
        ).replace("<", "\\u003c")
        injection += (
            f"<script {RUNTIME_CONFIG_SENTINEL}>"
            f"window.__LEMMA_CONFIG__={payload};</script>"
        )
    if SOCIAL_METADATA_SENTINEL not in text:
        injection += _public_app_social_metadata(app)
    if APP_BRANDING_SENTINEL not in text:
        injection += _public_app_branding_script(branding)
    if not injection:
        return text.encode("utf-8")

    lowered = text.lower()
    head_idx = lowered.find("<head")
    if head_idx != -1:
        tag_end = text.find(">", head_idx)
        if tag_end != -1:
            text = text[: tag_end + 1] + injection + text[tag_end + 1 :]
            return text.encode("utf-8")
    return (injection + text).encode("utf-8")
