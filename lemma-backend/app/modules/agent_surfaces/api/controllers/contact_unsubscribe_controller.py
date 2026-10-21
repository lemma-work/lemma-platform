"""How a contact stops a pod's email: the signed link every email to them
carries, the page it opens, and the one-click ``POST`` a mail client sends for
the ``List-Unsubscribe`` header (RFC 8058).

The link is a ``GET`` that only asks: mail scanners follow every link in a
message, and an unsubscribe on ``GET`` would unsubscribe whoever's mail they
scanned. The ``POST`` -- the page's button, or the client's one-click with
``List-Unsubscribe=One-Click`` in its body and the token in the URL -- does it.
What it records is ``contacts``': an opt-out kept by the handle's hash."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.contacts.contracts.outreach import (
    opt_out_by_token,
    read_unsubscribe_token,
)

public_router = APIRouter(prefix="/public/contacts", tags=["Contacts"])


def _page(
    heading: str, body: str, form: str = "", *, status_code: int = 200
) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport "
        'content="width=device-width,initial-scale=1"><title>Email preferences'
        '</title><body style="font:16px/1.5 system-ui,sans-serif;max-width:32rem;'
        'margin:4rem auto;padding:0 1rem;color:#1d1b18">'
        f'<h1 style="font-weight:500;font-size:1.4rem">{escape(heading)}</h1>'
        f"<p>{escape(body)}</p>{form}</body>",
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


@public_router.get(
    "/unsubscribe",
    operation_id="public.contact.unsubscribe.page",
    include_in_schema=False,
)
async def unsubscribe_page(token: str = Query(max_length=256)) -> HTMLResponse:
    if read_unsubscribe_token(token) is None:
        return _page("This link doesn't work", "It may have been copied incompletely.")
    return _page(
        "Stop these emails?",
        "You won't get messages like this at this address again, unless you "
        "write to us first.",
        '<form method="post"><input type="hidden" name="token" '
        f'value="{escape(token)}"><button style="font:inherit;padding:.5rem 1rem">'
        "Unsubscribe</button></form>",
    )


@public_router.post(
    "/unsubscribe",
    operation_id="public.contact.unsubscribe",
    include_in_schema=False,
)
async def unsubscribe(
    token: str | None = Form(default=None, max_length=256),
    query_token: str | None = Query(default=None, alias="token", max_length=256),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> HTMLResponse:
    target = read_unsubscribe_token(token or query_token or "")
    if target is None:
        return _page("This link doesn't work", "It may have been copied incompletely.")
    async with uow_factory() as uow:
        opted_out = await opt_out_by_token(uow, target)
        await uow.commit()
    if opted_out is None:
        # Signed, but for a workspace that has since been deleted.
        return _page(
            "This link no longer works",
            "The workspace that sent these emails has been deleted, so nothing "
            "more will come from it.",
            status_code=404,
        )
    return _page("You're unsubscribed", "You won't get emails like this again.")


__all__ = ["public_router"]
