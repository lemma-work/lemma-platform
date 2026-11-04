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
from contextlib import AsyncExitStack
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, JsonValue

from app.core.api.dependencies import get_uow_factory
from app.core.api.schemas import ErrorResponse
from app.core.api.streaming_multipart import (
    MultipartFileLimit,
    UploadStagingCoordinator,
    stream_multipart_form,
)
from app.core.domain.errors import (
    BadRequestError,
    PayloadTooLargeError,
    UploadCapacityExceededError,
)
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts.visitor_stream import visitor_frames
from app.modules.agent_surfaces.api import kept_chats
from app.modules.agent_surfaces.api.public_dependencies import (
    OptionalVisitorDep,
    PublicVisitorDep,
    PublicWidgetDep,
    VisitorAddressDep,
    web_chat,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.web_widgets import WebChatRefused, refused
from app.modules.agent_surfaces.services.outsider_limits import WebWidgetLimiter
from app.modules.agent_surfaces.services.web_chat import MAX_MESSAGE_CHARS, WebChat
from app.modules.agent_surfaces.services.web_visitors import (
    StartedSession,
    WebVisitors,
)
from app.modules.datastore.contracts.public_rows import (
    Attachment,
    OpenTable,
    too_large,
)

router = APIRouter(prefix="/public/web", tags=["Agent Surfaces (Web)"])

#: Files from people outside are staged in slots of their own: a stranger's
#: slow upload must never be what keeps a member's from starting.
_visitor_uploads = UploadStagingCoordinator(
    max_requests=4, max_active_bytes=128 * 1024 * 1024
)


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
    client_nonce: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "The page's own name for this message, echoed on it in history, so"
            " the page can tell its copy from the server's without comparing text."
        ),
    )


class Accepted(BaseModel):
    ok: bool = True


class HistoryMessage(BaseModel):
    role: str
    text: str
    sequence: int
    client_nonce: str | None = None
    from_person: bool = Field(
        default=False,
        description="A person on the team wrote it, not the bot.",
    )


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


class PublicColumnItem(BaseModel):
    name: str
    type: str


class PublicRowsResponse(BaseModel):
    table: str
    columns: list[PublicColumnItem]
    rows: list[dict[str, JsonValue]] = Field(
        description=(
            "Every row, at most 500. Dates and times are ISO 8601; a JSON column "
            "is its own lists and objects."
        )
    )
    truncated: bool = Field(
        description="More than 500 rows matched, so these are the first of them."
    )


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
    request: Request,
    response: Response,
    widget: PublicWidgetDep,
    address: VisitorAddressDep,
    visitors: WebVisitors = Depends(_visitors),
    chat: WebChat = Depends(web_chat),
) -> SessionResponse:
    """Start a session, or come back to one: either way, a new access token.

    On a page Lemma hosts, a visitor nobody signed in comes back to their chat
    from its cookie when the page has no secret to send.
    """
    on_pages = kept_chats.keeps_chats(request)
    keeps = on_pages and body.host_token is None
    kept = (
        kept_chats.kept_secret(request, widget) if keeps and not body.secret else None
    )
    secret = body.secret or kept
    try:
        started = await visitors.start_visitor_session(
            widget,
            secret=secret,
            host_token=body.host_token,
            altcha=body.altcha,
            address=address,
        )
    except WebChatRefused as ended:
        if kept is None or ended.code != "no_session":
            raise
        # The chat the cookie kept has ended: start a new one, as if none was.
        started = await visitors.start_visitor_session(
            widget, secret=None, host_token=None, altcha=body.altcha, address=address
        )
    if keeps and started.secret:
        kept_chats.keep(response, widget, started.secret)
    elif on_pages and body.host_token is not None:
        # Somebody signed in: their chat must not outlive that on this machine.
        kept_chats.forget(response, widget)
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
    await chat.send_visitor_message(
        widget, visitor, text=body.text, client_nonce=body.client_nonce
    )
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
            HistoryMessage(
                role=m.role,
                text=m.text,
                sequence=m.sequence,
                client_nonce=m.client_nonce,
                from_person=m.from_person,
            )
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


@router.get(
    "/{public_key}/rows",
    operation_id="public.web.rows.read",
    response_model=PublicRowsResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Not a Public table: missing, not Public, or per-member",
        },
        422: {"model": ErrorResponse, "description": "No such column to order by"},
        429: {"model": ErrorResponse, "description": "Too many reads"},
    },
)
async def web_read_rows(
    widget: PublicWidgetDep,
    visitor: OptionalVisitorDep,
    address: VisitorAddressDep,
    table: str = Query(max_length=255),
    order_by: str | None = Query(default=None, max_length=255),
    desc: bool = False,
    chat: WebChat = Depends(web_chat),
) -> PublicRowsResponse:
    """Read a table the pod marked Public: every row, at most 500.

    The same reading the pod's chat does for this visitor -- Public, and
    nothing else. A booking page reads its free slots here.
    """
    read = await chat.public_rows(
        widget,
        visitor,
        table=table,
        address=address,
        order_by=order_by,
        descending=desc,
    )
    return PublicRowsResponse(
        table=read.table,
        columns=[PublicColumnItem(name=c.name, type=c.type) for c in read.columns],
        rows=read.rows,
        truncated=read.truncated,
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


@router.post(
    "/{public_key}/rows/with-files",
    operation_id="public.web.row.add_with_files",
    status_code=201,
    response_model=Accepted,
    # The widget script's own wire, like the page and the script itself: a page
    # attaches a file through ``Lemma.addRow``, never by calling this.
    include_in_schema=False,
)
async def web_add_row_with_files(
    request: Request,
    widget: PublicWidgetDep,
    visitor: OptionalVisitorDep,
    address: VisitorAddressDep,
    table: str = Query(max_length=255),
    chat: WebChat = Depends(web_chat),
) -> Accepted:
    """Add one row with files attached. Nothing is read back.

    ``multipart/form-data``: the typed answers as a JSON object in ``values``,
    and at most one file per open file column, under the column's name. Who
    may send is settled before the body is read, so a refused visitor's files
    are never taken; past that, each file is held to its own cap and the
    whole response to another.
    """
    opened = await chat.admit_row(widget, visitor, table=table, address=address)
    attachable = [column.name for column in opened.columns if column.attached]
    async with AsyncExitStack() as stack:
        try:
            form = await stack.enter_async_context(
                stream_multipart_form(
                    request,
                    file_limits={
                        name: MultipartFileLimit(
                            max_bytes=surface_settings.surface_web_attachment_max_bytes,
                            label=name,
                        )
                        for name in attachable
                    },
                    # A file input left empty may arrive as a bare field of its
                    # column's name: taken, and never read as a value.
                    text_fields={"values", *attachable},
                    combined_max_bytes=surface_settings.surface_web_attachments_max_bytes,
                    coordinator=_visitor_uploads,
                )
            )
        except PayloadTooLargeError as exc:
            raise _too_large(opened, exc) from exc
        except UploadCapacityExceededError as exc:
            raise refused(
                "Too many files are arriving right now. Try again in a minute.",
                503,
                "busy",
            ) from exc
        except BadRequestError as exc:
            raise refused(
                "This form can't take that. Check your answers and try again.",
                400,
                "bad_answer",
            ) from exc
        attachments = {
            name: Attachment(name=staged.filename or name, content=staged.path)
            for name in attachable
            # An empty file input still sends a part: no name, no bytes.
            if (staged := form.file(name)) is not None and staged.size > 0
        }
        await chat.save_visitor_row(
            widget,
            visitor,
            table=table,
            answers=_typed_answers(form.text("values")),
            attachments=attachments,
        )
    return Accepted()


def _typed_answers(raw: str | None) -> dict[str, object]:
    """The JSON object a multipart row carries its typed answers in."""
    try:
        answers = json.loads(raw) if raw else {}
    except ValueError:
        answers = None
    if not isinstance(answers, dict):
        raise refused("Check your answers and try again", 400, "bad_answer")
    return answers


def _too_large(opened: OpenTable, exc: PayloadTooLargeError) -> WebChatRefused:
    """Over a cap, said for the file that was, or for the whole response."""
    details = exc.details if isinstance(exc.details, dict) else {}
    field = str(details.get("field"))
    by_name = {column.name: column for column in opened.columns}
    if field == "values":
        return refused("Your answers are too long", 413, "too_large")
    max_bytes = int(details.get("max_bytes") or 0)
    return refused(too_large(by_name.get(field), max_bytes).message, 413, "too_large")
