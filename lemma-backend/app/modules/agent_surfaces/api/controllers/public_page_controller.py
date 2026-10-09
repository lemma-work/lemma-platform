"""The widget script, and the pages Lemma hosts for people without a website.

``/public/web/{key}/page`` is the pod's chat on a page of its own.
``/public/web/{key}/page?table=signups`` is a form for a table the pod opened
to visitors, with the chat beside it to answer questions and fill it in. Both
are drawn by ``widget.js`` from what the API says; the page itself carries no
markup a member wrote, so one pod's page can never script another's. A pod that
wants its own design builds an app that adds rows the same way.

With ``PUBLIC_PAGES_URL`` set, pages are served on that origin only: it shares
no cookies with the API, so a page that could somehow be made to run a script
would find nothing of a member's there. Every page says who may frame it --
the widget's own allowed origins, or nobody -- so a site the widget never named
cannot dress the page up as its own.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from app.core.public_web import hosted_pages_host, public_web_enabled
from app.modules.agent_surfaces.api.public_dependencies import web_chat
from app.modules.agent_surfaces.domain.web_widgets import WebChatRefused, WebWidget
from app.modules.agent_surfaces.services.web_chat import WebChat

router = APIRouter(prefix="/public/web", tags=["Agent Surfaces (Web)"])

_WIDGET_JS = Path(__file__).resolve().parents[2] / "public" / "widget.js"
#: A table name a page may carry: datastore names are identifiers.
_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_CSP = (
    "default-src 'none'; script-src 'self'; connect-src 'self'; "
    "style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; "
    "form-action 'none'"
)
_PAGE_HEADERS = {
    "Content-Security-Policy": _CSP + "; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "public, max-age=60",
}


def _headers_for(widget: WebWidget) -> dict[str, str]:
    """The page's headers, letting only the widget's own sites frame it."""
    ancestors = " ".join(widget.allowed_origins) or "'none'"
    return {
        **_PAGE_HEADERS,
        "Content-Security-Policy": f"{_CSP}; frame-ancestors {ancestors}",
    }


def _on_pages_host(request: Request) -> bool:
    """Whether the request reached the origin hosted pages are served on."""
    expected = hosted_pages_host()
    return expected is None or request.headers.get("host", "").lower() == expected


@router.get(
    "/widget.js", operation_id="public.web.widget_script", include_in_schema=False
)
async def web_widget_script() -> Response:
    if not public_web_enabled():
        return Response(status_code=404)
    return Response(
        _WIDGET_JS.read_bytes(),
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


def _page(title: str, public_key: str, *, table: str | None) -> str:
    safe_title = html.escape(title)
    safe_key = html.escape(public_key, quote=True)
    table_attr = (
        f' data-lemma-table="{html.escape(table, quote=True)}"' if table else ""
    )
    chrome = (
        '<footer>Made with <a href="https://lemma.work" rel="noopener">Lemma</a>'
        " &middot; Don't share passwords or card numbers here.</footer>"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{safe_title}</title>
<style>
:root{{color-scheme:light dark}}
html,body{{margin:0;min-height:100%;background:#f6f4f1;
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#6f6a62}}
@media (prefers-color-scheme:dark){{html,body{{background:#141312;color:#a29d95}}}}
footer{{text-align:center;font-size:12px;padding:20px 16px 28px}}
footer a{{color:inherit}}
</style></head>
<body><main id="lemma-page"></main>{chrome}
<script src="/public/web/widget.js" data-lemma-key="{safe_key}"{table_attr} data-lemma-page></script>
</body></html>"""


def _missing() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><title>Not available</title><p>This page is not available.</p>",
        status_code=404,
        headers=_PAGE_HEADERS,
    )


@router.get(
    "/{public_key}/page", operation_id="public.web.page", include_in_schema=False
)
async def web_hosted_page(
    public_key: str, request: Request, chat: WebChat = Depends(web_chat)
) -> Response:
    """The chat, or a table's form with the chat beside it, on a page of its own."""
    table = request.query_params.get("table") or None
    if not public_web_enabled() or not _on_pages_host(request):
        return _missing()
    if table is not None and not _TABLE.match(table):
        return _missing()
    try:
        widget = await chat.widget_for_key(public_key)
        title = await chat.widget_title(widget)
        if table is not None:
            await chat.visitor_table(widget, table=table)
    except WebChatRefused:
        return _missing()
    return HTMLResponse(
        _page(title, widget.public_key, table=table), headers=_headers_for(widget)
    )
