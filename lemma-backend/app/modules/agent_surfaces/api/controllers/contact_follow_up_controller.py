"""Writing first to a contact, and the link a contact uses to make it stop.

Following up takes ``contact.message``, an editor's: writing first to somebody
outside the pod speaks for the pod, which writing in one's own conversations
does not. The unsubscribe page
is public: the signed token in its link is the credential, and it names one
handle. Opening the link only asks; the button does it -- mail scanners open
links, and a person should not be unsubscribed by their spam filter.
"""

from __future__ import annotations

from html import escape
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.services.contact_follow_ups import (
    FollowUpChannel,
    FollowUpRefused,
    handle_for_unsubscribe,
    send_follow_up,
)
from app.modules.contacts.contracts import unsubscribe_handle

router = APIRouter(prefix="/pods/{pod_id}/contacts", tags=["Contacts"])
public_router = APIRouter(prefix="/public/contacts", tags=["Contacts"])


class FollowUpRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    channel: FollowUpChannel = Field(
        default=FollowUpChannel.LATEST,
        description=(
            "`latest` writes in the contact's most recent conversation. "
            "`email` sends to their verified email address: in that "
            "conversation when it is an email thread, otherwise in a new thread "
            "from the pod's email address that answers contacts."
        ),
    )


class FollowUpResponse(BaseModel):
    conversation_id: UUID
    platform: str
    delivered: bool = Field(
        description=(
            "Handed to the platform now. False for a web chat, where the "
            "message waits for the contact's next visit."
        )
    )


@router.post(
    "/{contact_id}/messages",
    operation_id="contact.follow_up",
    response_model=FollowUpResponse,
    dependencies=[require_action(Permissions.CONTACT_MESSAGE)],
)
async def follow_up_contact(
    pod_id: UUID,
    contact_id: UUID,
    request: FollowUpRequest,
    user: CurrentUser,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> FollowUpResponse:
    """Write to a contact in their most recent conversation, where the channel allows.

    With ``channel: email``, to their verified email address instead, which
    reaches a web chat contact who is not on the page.

    Refused (409) when they unsubscribed there, when WhatsApp's 24-hour window
    has closed, when they have never written to the pod, or, for email, when
    they have no verified address or the pod no email address answering
    contacts; 429 past the day's follow-ups for this contact; 502 when the
    platform did not take it, which the conversation then shows as not sent.
    """
    sent = await send_follow_up(
        uow_factory,
        pod_id=pod_id,
        contact_id=contact_id,
        message=request.message,
        sent_by_user_id=user.id,
        channel=request.channel,
    )
    return FollowUpResponse(
        conversation_id=sent.conversation_id,
        platform=sent.platform,
        delivered=sent.delivered,
    )


def _page(heading: str, body: str, form: str = "") -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport "
        'content="width=device-width,initial-scale=1"><title>Email preferences'
        '</title><body style="font:16px/1.5 system-ui,sans-serif;max-width:32rem;'
        'margin:4rem auto;padding:0 1rem;color:#1d1b18">'
        f'<h1 style="font-weight:500;font-size:1.4rem">{escape(heading)}</h1>'
        f"<p>{escape(body)}</p>{form}</body>",
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


@public_router.get(
    "/unsubscribe",
    operation_id="public.contact.unsubscribe.page",
    include_in_schema=False,
)
async def unsubscribe_page(token: str = Query(max_length=256)) -> HTMLResponse:
    if handle_for_unsubscribe(token) is None:
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
    token: str = Form(max_length=256),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> HTMLResponse:
    handle_id = handle_for_unsubscribe(token)
    if handle_id is None:
        return _page("This link doesn't work", "It may have been copied incompletely.")
    async with uow_factory() as uow:
        await unsubscribe_handle(uow, handle_id=handle_id)
        await uow.commit()
    return _page("You're unsubscribed", "You won't get emails like this again.")


__all__ = ["FollowUpRefused", "public_router", "router"]
