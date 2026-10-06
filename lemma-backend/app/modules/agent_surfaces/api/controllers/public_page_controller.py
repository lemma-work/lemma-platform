"""The widget script, and the pages Lemma hosts for people without a website.

``/public/web/{key}/page`` is the pod's chat on a page of its own.
``/public/web/{key}/page?table=signups`` is a form for a table the pod opened
to visitors, with the chat beside it to answer questions and fill it in. Both
are drawn by ``widget.js`` from what the API says; the page itself carries no
markup a member wrote, so one pod's page can never script another's. A pod that
wants its own design builds an app that adds rows the same way.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.services.web_chat import WebChat, WebChatRefused

router = APIRouter(prefix="/public/web", tags=["Agent Surfaces (Web)"])

_WIDGET_JS = Path(__file__).resolve().parents[2] / "public" / "widget.js"
#: A table name a page may carry: datastore names are identifiers.
_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; connect-src 'self'; "
        "style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; "
        "form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "public, max-age=60",
}


def _chat(uow_factory: UnitOfWorkFactory = Depends(get_uow_factory)) -> WebChat:
    return WebChat(uow_factory)


@router.get(
    "/widget.js", operation_id="public.web.widget_script", include_in_schema=False
)
async def web_widget_script() -> Response:
    return Response(
        _WIDGET_JS.read_bytes(),
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


def _page(title: str, public_key: str, *, table: str | None, embedded: bool) -> str:
    safe_title = html.escape(title)
    safe_key = html.escape(public_key, quote=True)
    table_attr = (
        f' data-lemma-table="{html.escape(table, quote=True)}"' if table else ""
    )
    chrome = (
        ""
        if embedded
        else (
            '<footer>Made with <a href="https://lemma.work" rel="noopener">Lemma</a>'
            " &middot; Don't share passwords or card numbers here.</footer>"
        )
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
    public_key: str, request: Request, chat: WebChat = Depends(_chat)
) -> Response:
    """The chat, or a table's form with the chat beside it, on a page of its own."""
    table = request.query_params.get("table") or None
    if table is not None and not _TABLE.match(table):
        return _missing()
    try:
        widget = await chat.widget_for_key(public_key)
        title = await chat.widget_title(widget)
        if table is not None:
            await chat.visitor_table(widget, token=None, table=table)
    except WebChatRefused:
        return _missing()
    embedded = request.query_params.get("embed") == "1"
    return HTMLResponse(
        _page(title, widget.public_key, table=table, embedded=embedded),
        headers=_PAGE_HEADERS,
    )
