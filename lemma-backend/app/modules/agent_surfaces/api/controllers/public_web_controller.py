"""The public endpoints a web widget calls, with nothing but its public key.

Unauthenticated by design (`/public/web` is excluded from session auth): the
public key names the widget, and ``services/web_chat`` decides what a visitor
may do with it.

Bodies are JSON sent as ``text/plain``, and the session token rides in the body.
That keeps every request "simple" in CORS terms, so a browser never sends a
pre-flight the app-wide CORS policy would refuse for a customer's origin. Each
response then names the page's origin itself -- only when the widget allows it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from app.core.api.dependencies import get_uow_factory
from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts.visitor_stream import visitor_frames
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.web_widgets import WebWidget, normalize_origin
from app.modules.agent_surfaces.services.web_chat import WebChat, WebChatRefused

router = APIRouter(prefix="/public/web", tags=["Agent Surfaces (Web)"])

_MAX_BODY_BYTES = 65_536


class SessionRequest(BaseModel):
    host_token: str | None = Field(default=None, max_length=4096)


class MessageRequest(BaseModel):
    session: str = Field(max_length=128)
    text: str = Field(max_length=8000)


class StreamRequest(BaseModel):
    session: str = Field(max_length=128)


class HistoryRequest(BaseModel):
    session: str = Field(max_length=128)
    after: int = -1


class CodeRequest(BaseModel):
    session: str = Field(max_length=128)
    email: str = Field(max_length=320)


class VerifyRequest(CodeRequest):
    code: str = Field(max_length=12)


class TableRequest(BaseModel):
    session: str | None = Field(default=None, max_length=128)
    table: str = Field(max_length=255)


class RowRequest(TableRequest):
    values: dict[str, object] = Field(default_factory=dict)


def _chat(uow_factory: UnitOfWorkFactory = Depends(get_uow_factory)) -> WebChat:
    return WebChat(uow_factory)


def _own_origin() -> str:
    """Where Lemma serves a widget's hosted page, which every widget allows."""
    url = str(settings.api_url)
    scheme, _, rest = url.partition("://")
    return normalize_origin(f"{scheme}://{rest.split('/', 1)[0]}")


def _allowed(widget: WebWidget, origin: str) -> bool:
    return normalize_origin(origin) == _own_origin() or widget.allows_origin(origin)


def _cors(request: Request, widget: WebWidget | None) -> dict[str, str]:
    origin = request.headers.get("origin")
    if not origin or widget is None or not _allowed(widget, origin):
        return {}
    return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}


def _address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _body[T: BaseModel](request: Request, model: type[T]) -> T:
    # Read as it arrives and stop at the cap: the sender chooses the size, and a
    # key copied off a page must not be a way to make the server hold a body.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > _MAX_BODY_BYTES:
            raise WebChatRefused("That is too long", status_code=413, code="too_large")
    try:
        return model.model_validate_json(bytes(raw) or b"{}")
    except ValidationError as exc:
        raise WebChatRefused(
            "That request was not understood", status_code=400, code="bad_request"
        ) from exc


async def _widget_for(request: Request, chat: WebChat, public_key: str) -> WebWidget:
    widget = await chat.widget_for_key(public_key)
    origin = request.headers.get("origin")
    if origin and not _allowed(widget, origin):
        raise WebChatRefused(
            "This widget is not allowed on this site", status_code=403, code="origin"
        )
    return widget


def _refusal(
    request: Request, widget: WebWidget | None, exc: WebChatRefused
) -> JSONResponse:
    return JSONResponse(
        {"error": exc.message, "code": exc.code},
        status_code=exc.status_code,
        headers=_cors(request, widget),
    )


@router.post("/{public_key}/session", operation_id="public.web.session.start")
async def web_start_session(
    public_key: str, request: Request, chat: WebChat = Depends(_chat)
) -> JSONResponse:
    widget = None
    try:
        widget = await _widget_for(request, chat, public_key)
        body = await _body(request, SessionRequest)
        started = await chat.start_visitor_session(
            widget, host_token=body.host_token, address=_address(request)
        )
        title = await chat.widget_title(widget)
    except WebChatRefused as exc:
        return _refusal(request, widget, exc)
    return JSONResponse(
        {
            "session": started.token,
            "is_contact": started.is_contact,
            "display_name": started.display_name,
            "title": title,
        },
        headers=_cors(request, widget),
    )


@router.post("/{public_key}/messages", operation_id="public.web.message.send")
async def web_send_message(
    public_key: str, request: Request, chat: WebChat = Depends(_chat)
) -> JSONResponse:
    widget = None
    try:
        widget = await _widget_for(request, chat, public_key)
        body = await _body(request, MessageRequest)
        await chat.send_visitor_message(widget, token=body.session, text=body.text)
    except WebChatRefused as exc:
        return _refusal(request, widget, exc)
    return JSONResponse({"ok": True}, status_code=202, headers=_cors(request, widget))


@router.post("/{public_key}/stream", operation_id="public.web.stream.read")
async def web_stream_answers(
    public_key: str,
    request: Request,
    chat: WebChat = Depends(_chat),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response:
    """The bot's answer as it is written, one JSON object per line.

    Read with ``fetch`` rather than ``EventSource`` so the session stays in the
    body and the request stays simple. Only what a visitor may see is sent:
    see ``agent.contracts.visitor_stream``. The page reconnects when it closes.
    """
    widget = None
    try:
        widget = await _widget_for(request, chat, public_key)
        body = await _body(request, StreamRequest)
        conversation_id = await chat.visitor_conversation(widget, token=body.session)
    except WebChatRefused as exc:
        return _refusal(request, widget, exc)
    if conversation_id is None:
        return Response(status_code=204, headers=_cors(request, widget))
    return StreamingResponse(
        _lines(conversation_id, uow_factory),
        media_type="application/x-ndjson",
        headers={
            **_cors(request, widget),
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


async def _lines(
    conversation_id: UUID, uow_factory: UnitOfWorkFactory
) -> AsyncIterator[str]:
    yield _line({"type": "open"})
    async for frame in visitor_frames(
        conversation_id,
        uow_factory=uow_factory,
        max_seconds=surface_settings.surface_web_stream_seconds,
    ):
        yield "\n" if frame is None else _line(frame)


def _line(frame: dict[str, object]) -> str:
    """One frame on the wire. A delta is a few words, a message one answer."""
    return json.dumps(frame) + "\n"


@router.post("/{public_key}/history", operation_id="public.web.history.read")
async def web_read_history(
    public_key: str, request: Request, chat: WebChat = Depends(_chat)
) -> JSONResponse:
    widget = None
    try:
        widget = await _widget_for(request, chat, public_key)
        body = await _body(request, HistoryRequest)
        messages = await chat.visitor_history(
            widget, token=body.session, after=body.after
        )
    except WebChatRefused as exc:
        return _refusal(request, widget, exc)
    return JSONResponse(
        {
            "messages": [
                {"role": m.role, "text": m.text, "sequence": m.sequence}
                for m in messages
            ]
        },
        headers=_cors(request, widget),
    )


@router.post("/{public_key}/code", operation_id="public.web.code.send")
async def web_send_code(
    public_key: str, request: Request, chat: WebChat = Depends(_chat)
) -> JSONResponse:
    widget = None
    try:
        widget = await _widget_for(request, chat, public_key)
        body = await _body(request, CodeRequest)
        await chat.send_visitor_code(
            widget, token=body.session, email=body.email, address=_address(request)
        )
    except WebChatRefused as exc:
        return _refusal(request, widget, exc)
    return JSONResponse({"ok": True}, headers=_cors(request, widget))


@router.post("/{public_key}/code/verify", operation_id="public.web.code.verify")
async def web_verify_code(
    public_key: str, request: Request, chat: WebChat = Depends(_chat)
) -> JSONResponse:
    widget = None
    try:
        widget = await _widget_for(request, chat, public_key)
        body = await _body(request, VerifyRequest)
        verified = await chat.verify_visitor_code(
            widget, token=body.session, email=body.email, code=body.code
        )
    except WebChatRefused as exc:
        return _refusal(request, widget, exc)
    return JSONResponse(
        {"is_contact": verified.is_contact, "display_name": verified.display_name},
        headers=_cors(request, widget),
    )


@router.post("/{public_key}/table", operation_id="public.web.table.read")
async def web_read_table(
    public_key: str, request: Request, chat: WebChat = Depends(_chat)
) -> JSONResponse:
    """What a page may ask for on a table the pod opened to visitors.

    The open columns only, in order: enough to draw a form, and nothing else
    about the table or its rows.
    """
    widget = None
    try:
        widget = await _widget_for(request, chat, public_key)
        body = await _body(request, TableRequest)
        opened, contacts_only = await chat.visitor_table(
            widget, token=body.session, table=body.table
        )
    except WebChatRefused as exc:
        return _refusal(request, widget, exc)
    return JSONResponse(
        {
            "table": opened.name,
            "contacts_only": contacts_only,
            "columns": [
                {
                    "name": column.name,
                    "type": column.type,
                    "required": column.required,
                    "options": list(column.options),
                    "description": column.description,
                }
                for column in opened.columns
            ],
        },
        headers=_cors(request, widget),
    )


@router.post("/{public_key}/rows", operation_id="public.web.row.add")
async def web_add_row(
    public_key: str, request: Request, chat: WebChat = Depends(_chat)
) -> JSONResponse:
    """Add one row to a table the pod opened to visitors. Nothing is read back."""
    widget = None
    try:
        widget = await _widget_for(request, chat, public_key)
        body = await _body(request, RowRequest)
        await chat.add_visitor_row(
            widget,
            token=body.session,
            table=body.table,
            answers=body.values,
            address=_address(request),
        )
    except WebChatRefused as exc:
        return _refusal(request, widget, exc)
    return JSONResponse({"ok": True}, status_code=201, headers=_cors(request, widget))
