"""The public endpoints a web widget calls, with nothing but its public key.

Unauthenticated by the platform (``/public`` is excluded from session auth):
the public key names the widget (``PublicWidgetDep``), and the visitor's own
access token names them (``PublicVisitorDep``). ``services/web_visitors`` and
``services/web_chat`` decide what a visitor may do.

Bodies are ordinary JSON and the token rides in ``Authorization``, so the
browser pre-flights each call. ``PublicWebCORSMiddleware`` answers those from
the widget's own allowed origins, and puts the page's origin on every response
-- errors included -- only when the widget allows it. Errors use the API's usual
envelope, ``{"message", "code", ...}``, with ``code`` the word a page acts on.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts.visitor_stream import visitor_frames
from app.modules.agent_surfaces.api.public_dependencies import (
    OptionalVisitorDep,
    PublicVisitorDep,
    PublicWidgetDep,
    VisitorAddressDep,
    web_chat,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.web_widgets import refused
from app.modules.agent_surfaces.services.outsider_limits import WebWidgetLimiter
from app.modules.agent_surfaces.services.web_chat import MAX_MESSAGE_CHARS, WebChat
from app.modules.agent_surfaces.services.web_visitors import (
    StartedSession,
    WebVisitors,
)

router = APIRouter(prefix="/public/web", tags=["Agent Surfaces (Web)"])


class SessionRequest(BaseModel):
    secret: str | None = Field(
        default=None,
        max_length=128,
        description="The secret a previous answer returned, to come back to that session.",
    )
    host_token: str | None = Field(
        default=None,
        max_length=4096,
        description="A token the page's own server signed with the widget's secret.",
    )
    altcha: str | None = Field(
        default=None,
        max_length=4096,
        description="The solved challenge, when starting an anonymous session.",
    )


class SessionResponse(BaseModel):
    access_token: str = Field(
        description="Send as `Authorization: Bearer` on every other call. Never store it."
    )
    secret: str | None = Field(
        description="Only when new: keep it to come back. Null means keep the one you have."
    )
    is_contact: bool
    display_name: str | None
    title: str | None = None
    expires_in: int = Field(description="Seconds until the access token expires.")


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class Accepted(BaseModel):
    ok: bool = True


class HistoryMessage(BaseModel):
    role: str
    text: str
    sequence: int


class HistoryResponse(BaseModel):
    messages: list[HistoryMessage]


class CodeRequest(BaseModel):
    email: str = Field(max_length=320)
    altcha: str | None = Field(default=None, max_length=4096)


class VerifyRequest(BaseModel):
    email: str = Field(max_length=320)
    code: str = Field(max_length=12)


class TableColumn(BaseModel):
    name: str
    type: str
    required: bool
    options: list[str]
    description: str | None
    #: The form input that asks for this column (``email``, ``select``, ...),
    #: decided once by the datastore so every page draws it the same way.
    input: str


class TableResponse(BaseModel):
    table: str
    contacts_only: bool
    columns: list[TableColumn]


class RowRequest(BaseModel):
    table: str = Field(max_length=255)
    values: dict[str, object] = Field(default_factory=dict)


def _visitors(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> WebVisitors:
    return WebVisitors(uow_factory)


def _session_response(started: StartedSession, *, title: str | None) -> SessionResponse:
    return SessionResponse(
        access_token=started.access_token,
        secret=started.secret,
        is_contact=started.is_contact,
        display_name=started.display_name,
        title=title,
        expires_in=started.expires_in,
    )


@router.get("/{public_key}/challenge", operation_id="public.web.challenge.read")
async def web_challenge(
    widget: PublicWidgetDep,
    purpose: str = Query(default="session", pattern="^(session|code)$"),
    visitors: WebVisitors = Depends(_visitors),
) -> Mapping[str, object]:
    """A proof-of-work to solve before starting a session or asking for a code.

    ``{"enabled": false}`` when the deployment has bot protection off.
    """
    del widget  # resolved for the switch, the key and the origin
    return await visitors.visitor_challenge(for_code=purpose == "code")


@router.post(
    "/{public_key}/session",
    operation_id="public.web.session.start",
    response_model=SessionResponse,
)
async def web_start_session(
    body: SessionRequest,
    widget: PublicWidgetDep,
    address: VisitorAddressDep,
    visitors: WebVisitors = Depends(_visitors),
    chat: WebChat = Depends(web_chat),
) -> SessionResponse:
    """Start a session, or come back to one: either way, a new access token."""
    started = await visitors.start_visitor_session(
        widget,
        secret=body.secret,
        host_token=body.host_token,
        altcha=body.altcha,
        address=address,
    )
    return _session_response(started, title=await chat.widget_title(widget))


@router.post(
    "/{public_key}/messages",
    operation_id="public.web.message.send",
    status_code=202,
    response_model=Accepted,
)
async def web_send_message(
    body: MessageRequest,
    widget: PublicWidgetDep,
    visitor: PublicVisitorDep,
    chat: WebChat = Depends(web_chat),
) -> Accepted:
    await chat.send_visitor_message(widget, visitor, text=body.text)
    return Accepted()


@router.get("/{public_key}/stream", operation_id="public.web.stream.read")
async def web_stream_answers(
    visitor: PublicVisitorDep,
    chat: WebChat = Depends(web_chat),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response:
    """The bot's answer as it is written, one JSON object per line.

    The first line is ``{"type": "open"}``, a handshake that says the stream is
    up, not a frame of the conversation. Only what a visitor may see follows:
    see ``agent.contracts.visitor_stream``. The page reconnects when it closes.
    A session holds at most two streams at once.
    """
    conversation_id = await chat.visitor_conversation(visitor)
    if conversation_id is None:
        return Response(status_code=204)
    limiter = WebWidgetLimiter()
    session_id = visitor.session.session_id
    if not await limiter.open_visitor_stream(session_id=session_id):
        raise refused("This chat is already open elsewhere", 429, "too_many_streams")

    async def release() -> None:
        await limiter.close_visitor_stream(session_id=session_id)

    return StreamingResponse(
        _lines(conversation_id, uow_factory, release=release),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _lines(
    conversation_id: UUID,
    uow_factory: UnitOfWorkFactory,
    *,
    release: Callable[[], Awaitable[None]],
) -> AsyncIterator[str]:
    try:
        yield _line({"type": "open"})
        async for frame in visitor_frames(
            conversation_id,
            uow_factory=uow_factory,
            max_seconds=surface_settings.surface_web_stream_seconds,
        ):
            yield "\n" if frame is None else _line(frame)
    finally:
        await release()


def _line(frame: Mapping[str, object]) -> str:
    """One frame on the wire. A delta is a few words, a message one answer."""
    return json.dumps(frame) + "\n"


@router.get(
    "/{public_key}/history",
    operation_id="public.web.history.read",
    response_model=HistoryResponse,
)
async def web_read_history(
    visitor: PublicVisitorDep,
    after: int = Query(default=-1, ge=-1),
    chat: WebChat = Depends(web_chat),
) -> HistoryResponse:
    messages = await chat.visitor_history(visitor, after=after)
    return HistoryResponse(
        messages=[
            HistoryMessage(role=m.role, text=m.text, sequence=m.sequence)
            for m in messages
        ]
    )


@router.post(
    "/{public_key}/code", operation_id="public.web.code.send", response_model=Accepted
)
async def web_send_code(
    body: CodeRequest,
    widget: PublicWidgetDep,
    visitor: PublicVisitorDep,
    address: VisitorAddressDep,
    visitors: WebVisitors = Depends(_visitors),
) -> Accepted:
    await visitors.send_visitor_code(
        widget, visitor, email=body.email, altcha=body.altcha, address=address
    )
    return Accepted()


@router.post(
    "/{public_key}/code/verify",
    operation_id="public.web.code.verify",
    response_model=SessionResponse,
)
async def web_verify_code(
    body: VerifyRequest,
    widget: PublicWidgetDep,
    visitor: PublicVisitorDep,
    address: VisitorAddressDep,
    visitors: WebVisitors = Depends(_visitors),
) -> SessionResponse:
    """Confirm an email address: the session becomes a contact's, under a new
    secret and a new access token, which replace the old ones."""
    started = await visitors.verify_visitor_code(
        widget, visitor, email=body.email, code=body.code, address=address
    )
    return _session_response(started, title=None)


@router.get(
    "/{public_key}/table",
    operation_id="public.web.table.read",
    response_model=TableResponse,
)
async def web_read_table(
    widget: PublicWidgetDep,
    table: str = Query(max_length=255),
    chat: WebChat = Depends(web_chat),
) -> TableResponse:
    """What a page may ask for on a table the pod opened to visitors.

    The open columns only, in order: enough to draw a form, and nothing else
    about the table or its rows.
    """
    opened, contacts_only = await chat.visitor_table(widget, table=table)
    return TableResponse(
        table=opened.name,
        contacts_only=contacts_only,
        columns=[
            TableColumn(
                name=column.name,
                type=column.type,
                required=column.required,
                options=list(column.options),
                description=column.description,
                input=column.input,
            )
            for column in opened.columns
        ],
    )


@router.post(
    "/{public_key}/rows",
    operation_id="public.web.row.add",
    status_code=201,
    response_model=Accepted,
)
async def web_add_row(
    body: RowRequest,
    widget: PublicWidgetDep,
    visitor: OptionalVisitorDep,
    address: VisitorAddressDep,
    chat: WebChat = Depends(web_chat),
) -> Accepted:
    """Add one row to a table the pod opened to visitors. Nothing is read back."""
    await chat.add_visitor_row(
        widget, visitor, table=body.table, answers=body.values, address=address
    )
    return Accepted()
