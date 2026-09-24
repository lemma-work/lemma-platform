"""The parts of the Agent Host link that are not one connection's state.

Split from ``agent_host_link_session`` so the session reads as the lifecycle it
is: what a frame means, how its failure is answered, and how one whole frame is
written are here, where each can be read -- and tested -- on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.websockets import WebSocketDisconnect

from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    AgentHostCommand,
    AgentHostCommandRejection,
    AgentHostRunCheckpoint,
)
from app.modules.agent.domain.agent_host_link import (
    ControlBody,
    LinkErrorCode,
    LinkFrame,
    RefusedUpdate,
    ServerErrorBody,
    ServerFrameType,
)
from app.modules.agent.infrastructure.agent_host.repository_common import (
    AgentHostNotFound,
    AgentHostRepositoryError,
    AgentHostSequenceGap,
    AgentHostStaleLease,
    AgentHostTerminalRun,
)
from app.modules.agent.services.agent_host_link_mcp import LinkUnauthorized
from app.modules.agent.services.agent_host_link_store import ControlUpdates


logger = get_logger(__name__)


#: The standard WebSocket code for "the server hit an unexpected condition".
#: Not in the contract's table because it is not a protocol answer: it is what a
#: bug in the session looks like from the host, which reconnects with backoff.
INTERNAL_ERROR_CLOSE = 1011

#: Transient infrastructure failures: the request may well succeed if the host
#: sends it again, so they are answered UNAVAILABLE and retryable rather than
#: closing anything.
TRANSIENT_ERRORS = (
    RedisError,
    OperationalError,
    InterfaceError,
    PoolTimeoutError,
    OSError,
)

# Subclasses before their base: the first match wins.
_EVENT_ERROR_CODES: tuple[tuple[type[AgentHostRepositoryError], LinkErrorCode], ...] = (
    (AgentHostStaleLease, LinkErrorCode.STALE_LEASE),
    (AgentHostSequenceGap, LinkErrorCode.SEQUENCE_GAP),
    (AgentHostTerminalRun, LinkErrorCode.TERMINAL_RUN),
    (AgentHostNotFound, LinkErrorCode.NOT_FOUND),
)


class LinkSocket(Protocol):
    """The part of Starlette's ``WebSocket`` a session uses."""

    @property
    def headers(self) -> Mapping[str, str]: ...

    async def accept(self) -> None: ...

    async def receive(self) -> Mapping[str, object]: ...

    async def send_text(self, data: str) -> None: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


class LinkClose(Exception):
    """End the connection with this code; raised from anywhere in a handler."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


class LinkRequestError(Exception):
    """Answer the request in hand with an ``error`` frame and carry on."""

    def __init__(
        self, code: LinkErrorCode, message: str, *, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class EmptyBody(BaseModel):
    """The body of a frame that carries nothing, such as ``revoked``."""


def bearer_secret(headers: Mapping[str, str]) -> str | None:
    scheme, separator, token = (headers.get("authorization") or "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def validation_summary(exc: ValidationError) -> str:
    """Where a body failed validation, without echoing what it contained."""
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", ())) or "body"
    return f"{location}: {first.get('msg', 'invalid')}"


def event_error(exc: AgentHostRepositoryError) -> LinkRequestError:
    for kind, code in _EVENT_ERROR_CODES:
        if isinstance(exc, kind):
            return LinkRequestError(code, str(exc))
    return LinkRequestError(LinkErrorCode.INVALID_FRAME, str(exc))


def _item_ids(item: object) -> tuple[str | None, str | None]:
    if not isinstance(item, dict):
        return None, None
    command_id, run_id = item.get("command_id"), item.get("run_id")
    return (
        command_id if isinstance(command_id, str) else None,
        run_id if isinstance(run_id, str) else None,
    )


def _as_uuid(raw: object) -> UUID | None:
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def parse_control_items(
    body: ControlBody,
) -> tuple[ControlUpdates, list[RefusedUpdate]]:
    """Parse each reported update on its own, refusing only the ones that fail.

    One malformed checkpoint used to 422 the poll that carried every other
    update and the only commands that host could receive -- and the host
    bisected its outbox to find the culprit. Refusing it by name instead lets
    the host drop exactly that item and nothing else.
    """
    refused: list[RefusedUpdate] = []
    acknowledged: list[UUID] = []
    for index, raw in enumerate(body.acknowledged_command_ids):
        command_id = _as_uuid(raw)
        if command_id is not None:
            acknowledged.append(command_id)
            continue
        refused.append(
            RefusedUpdate(
                kind="ack",
                index=index,
                command_id=raw if isinstance(raw, str) else None,
                reason="not a command id",
            )
        )
    checkpoints = _parse_each(
        body.checkpoints, AgentHostRunCheckpoint, "checkpoint", refused
    )
    rejections = _parse_each(
        body.rejections, AgentHostCommandRejection, "rejection", refused
    )
    return (
        ControlUpdates(
            capacity=body.capacity,
            acknowledged_command_ids=acknowledged,
            checkpoints=checkpoints,
            rejections=rejections,
        ),
        refused,
    )


def _parse_each[ModelT: BaseModel](
    items: list[object],
    model: type[ModelT],
    kind: str,
    refused: list[RefusedUpdate],
) -> list[ModelT]:
    parsed: list[ModelT] = []
    for index, raw in enumerate(items):
        try:
            parsed.append(model.model_validate(raw))
        except ValidationError as exc:
            command_id, run_id = _item_ids(raw)
            refused.append(
                RefusedUpdate.model_validate(
                    {
                        "kind": kind,
                        "index": index,
                        "command_id": command_id,
                        "run_id": run_id,
                        "reason": validation_summary(exc),
                    }
                )
            )
    return parsed


class LinkWriter:
    """The one way a frame reaches the socket.

    A lock rather than a writer task: the frame goes out whole from whichever
    task produced it, never interleaved with another, and a socket that already
    went away is a state to remember rather than an error to raise in every
    handler that happened to be answering at the time.
    """

    def __init__(self, socket: LinkSocket, *, on_lost: Callable[[], None]) -> None:
        self._socket = socket
        self._lock = asyncio.Lock()
        self._on_lost = on_lost
        self.peer_gone = False

    async def send_frame(
        self,
        frame_type: ServerFrameType,
        body: BaseModel,
        *,
        re: str | None = None,
    ) -> None:
        text = LinkFrame(
            type=frame_type.value,
            re=re,
            body=body.model_dump(mode="json"),
        ).model_dump_json(exclude_none=True)
        async with self._lock:
            if self.peer_gone:
                return
            try:
                await self._socket.send_text(text)
            except WebSocketDisconnect, RuntimeError, OSError:
                self.lost()

    async def send_error(
        self,
        frame: LinkFrame,
        code: LinkErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        await self.send_frame(
            ServerFrameType.ERROR,
            ServerErrorBody(code=code, message=message, retryable=retryable),
            re=frame.id,
        )

    async def close_socket(self, code: int, reason: str) -> None:
        async with self._lock:
            if self.peer_gone:
                return
            try:
                await self._socket.close(code=code, reason=reason)
            except WebSocketDisconnect, RuntimeError, OSError:
                # Already gone from under us; there is nobody left to tell.
                self.lost()

    def lost(self) -> None:
        self.peer_gone = True
        self._on_lost()


class SentCommands:
    """Which commands one socket handed out, and when.

    The queue hands a DELIVERED command out again on every read until the host
    acknowledges it, and the push floor reads every 5 seconds. Without this, one
    START_RUN would be pushed a dozen times a minute while the host was busy
    starting it. The host de-duplicates by ``command_id`` regardless; this is
    about not sending what it would only throw away.
    """

    def __init__(self, resend_after_seconds: float) -> None:
        self._resend_after_seconds = resend_after_seconds
        self._sent_at: dict[UUID, float] = {}

    def unsent(self, commands: list[AgentHostCommand]) -> list[AgentHostCommand]:
        now = asyncio.get_running_loop().time()
        horizon = now - self._resend_after_seconds
        self._sent_at = {
            command_id: sent
            for command_id, sent in self._sent_at.items()
            if sent > horizon
        }
        fresh = [
            command for command in commands if command.command_id not in self._sent_at
        ]
        for command in fresh:
            self._sent_at[command.command_id] = now
        return fresh

    def acknowledged(self, command_ids: list[UUID]) -> None:
        for command_id in command_ids:
            self._sent_at.pop(command_id, None)


async def answer_request(
    frame: LinkFrame,
    handler: Callable[[LinkFrame], Awaitable[None]],
    *,
    writer: LinkWriter,
    stop: Callable[[int, str], None],
    host_id: UUID | None,
    connection_id: UUID,
) -> None:
    """Run one request's handler and turn its failure into an answer.

    The handler runs as a task so a bug in it is reported, with its traceback,
    and answered INTERNAL -- rather than ending the reader and with it every
    other request on the socket. The message is never the exception's: a frame
    is not a place for a traceback or an internal detail, and the log is where
    both go.
    """
    task = asyncio.ensure_future(
        _answering(frame, handler, writer=writer, stop=stop, host_id=host_id)
    )
    try:
        await asyncio.wait({task})
    except asyncio.CancelledError:
        # The link is closing: the request goes with it, unanswered, and the
        # host sends it again on the next link.
        task.cancel()
        raise
    if task.cancelled() or task.exception() is None:
        return
    logger.error(
        "agent.agent_host_link.request.failed",
        host_id=str(host_id) if host_id else None,
        connection_id=str(connection_id),
        frame_type=frame.type,
        exc_info=task.exception(),
    )
    await writer.send_error(
        frame,
        LinkErrorCode.INTERNAL,
        "Lemma could not handle this request",
        retryable=True,
    )


async def _answering(
    frame: LinkFrame,
    handler: Callable[[LinkFrame], Awaitable[None]],
    *,
    writer: LinkWriter,
    stop: Callable[[int, str], None],
    host_id: UUID | None,
) -> None:
    """The answers a handler can choose, as opposed to the ones it cannot."""
    try:
        await handler(frame)
    except LinkClose as close:
        stop(close.code, close.reason)
    except LinkRequestError as refusal:
        await writer.send_error(
            frame, refusal.code, refusal.message, retryable=refusal.retryable
        )
    except ValidationError as exc:
        await writer.send_error(
            frame, LinkErrorCode.INVALID_FRAME, validation_summary(exc)
        )
    except LinkUnauthorized:
        await writer.send_error(
            frame,
            LinkErrorCode.UNAUTHORIZED,
            "the token does not grant this conversation",
        )
    except TRANSIENT_ERRORS as exc:
        logger.warning(
            "agent.agent_host_link.request_unavailable.degraded",
            host_id=str(host_id) if host_id else None,
            frame_type=frame.type,
            exc_info=exc,
        )
        await writer.send_error(
            frame,
            LinkErrorCode.UNAVAILABLE,
            "Lemma is briefly unavailable",
            retryable=True,
        )
