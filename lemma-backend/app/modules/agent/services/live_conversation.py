"""A conversation's run on the pod's live socket: the ``conversation`` topic.

The web app watches a conversation's runs here rather than over the SSE
stream, which stays for the CLI, the SDKs and the Agent Host. The events are
the stream's own -- what ``realtime.publish_conversation_event`` publishes
(``token``, ``message``, ``status``, ``completed``, ``error``, ``stopped``,
``title`` …) -- each sent as one event of the subscription, unchanged, so a
client reads the same shapes either way.

Who may watch is who may read the conversation: pod membership and
``queries.get_conversation``, the check its stream endpoint makes. Run events
are not stored as a log, so the topic does not replay them: a resumed
subscriber is told to ``resync`` and refetches the conversation's messages
over REST. What the SSE stream does for its client is done here too: right
after ``subscribed`` comes a ``run`` event naming the run being watched (the
one asked for, else the active one) and its status, and a run that has
already finished is followed by its ending (``completed``, ``error`` or
``stopped``, as the stream replays it). While a run is watched and nothing is
heard for a while, it is read again, so an ending published while the
listener was away still arrives.

Access is decided again when the pod's grants move
(``authz_epoch.pod_grants_epoch``), and every ``LIVE_AUTHZ_RECHECK_SECONDS``
regardless: a subscriber who may no longer read the conversation -- taken out
of the pod, the conversation no longer theirs -- is sent ``unsubscribed``
(``access_revoked``) and hears nothing more of it.

The same events are a named stream, ``agent.conversation`` (key: the
conversation id), that core's live registry opens for other topics -- channels
relays an answer being written into the line it answers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.authz_epoch import pod_grants_epoch
from app.core.authorization.dependencies import assert_pod_membership
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.scope import context_scope
from app.core.domain.errors import DomainError
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.access_clock import AccessClock
from app.core.live.config import live_settings
from app.core.live.connection import LiveConnection
from app.core.live.protocol import (
    JsonObject,
    LiveAccessDenied,
    LiveErrorCode,
    ResyncReason,
    SubscribeRequest,
    UnsubscribedReason,
    dumps,
    parse_params,
)
from app.core.live.topics import LiveContribution
from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.modules.agent.domain.errors import (
    AgentNotFoundError,
    ConversationNotFoundError,
)
from app.modules.agent.domain.entities import AgentRun
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.realtime import (
    conversation_channel,
    run_ending_payload,
)
from app.modules.agent.services.service_factories import build_conversation_service

logger = get_logger(__name__)

#: The stream other topics open to follow a conversation's runs.
CONVERSATION_STREAM = "agent.conversation"
#: How long a subscribe waits for its Pub/Sub lease before answering anyway.
_LISTEN_TIMEOUT = 5.0


def _decoded(message: str | bytes) -> JsonObject | None:
    """One published event; small (a token, a status), so decoded in place."""
    try:
        payload = json.loads(message)
    except ValueError, TypeError:
        return None
    return payload if isinstance(payload, dict) else None


@asynccontextmanager
async def conversation_events(key: str) -> AsyncIterator[AsyncIterator[JsonObject]]:
    """A lease on one conversation's run events, decoded."""
    conversation_id = UUID(key)
    service = await get_channel_service()
    async with service.subscribe([conversation_channel(conversation_id)]) as raw:

        async def decoded() -> AsyncIterator[JsonObject]:
            async for message in raw:
                payload = _decoded(message)
                if payload is not None:
                    yield payload

        yield decoded()


#: How long a watched run may be silent before it is read again.
IDLE_CHECK_SECONDS = 15.0

#: A run's own events that end it.
_ENDINGS = frozenset({"completed", "error", "stopped"})


class _Params(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    conversation_id: UUID
    #: The run to watch -- one just started; else the conversation's active one.
    agent_run_id: UUID | None = None


@dataclass(eq=False, slots=True)
class _ConversationSub:
    id: str
    connection: LiveConnection
    conversation_id: UUID
    active: bool = True
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    #: The run being watched, until it ends.
    run_id: UUID | None = None
    #: The pod's grants epoch when access was decided at subscribe.
    epoch: str | None = None

    def release(self) -> None:
        if not self.active:
            return
        self.active = False
        if self.task is not None:
            self.task.cancel()


async def _authorize(
    pod_id: UUID, user_id: UUID, conversation_id: UUID, run_id: UUID | None
) -> AgentRun | None:
    """Refuse who may not read the conversation; the run to watch (the one
    named, else the active one), if there is one."""
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        try:
            ctx = await create_authorization_data_service(uow).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                assert_pod_membership(ctx, "use conversations in this pod")
                service = build_conversation_service(uow)
                await service.queries.get_conversation(
                    conversation_id=conversation_id, user_id=user_id, pod_id=pod_id
                )
                if run_id is None:
                    return await service.queries.get_active_agent_run(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        pod_id=pod_id,
                    )
                run = await service.runs.get_agent_run(run_id)
                if run is None or run.conversation_id != conversation_id:
                    raise LiveAccessDenied(LiveErrorCode.NOT_FOUND, "Run not found")
                return run
        except (AgentNotFoundError, ConversationNotFoundError) as missing:
            raise LiveAccessDenied(
                LiveErrorCode.NOT_FOUND, "Conversation not found"
            ) from missing
        except DomainError as refused:
            raise LiveAccessDenied(
                LiveErrorCode.NOT_FOUND
                if refused.status_code == 404
                else LiveErrorCode.FORBIDDEN,
                refused.message,
            ) from refused


async def _run_now(run_id: UUID) -> AgentRun | None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        return await Runs(uow).get_agent_run(run_id)


def _heard(sub: _ConversationSub, event: JsonObject) -> None:
    """Follow the run the events are about: a new one is watched, an ending
    ends the watch."""
    run = event.get("agent_run_id")
    if event.get("type") in _ENDINGS:
        if run is None or str(run) == str(sub.run_id):
            sub.run_id = None
        return
    if run is not None and event.get("type") == "status":
        with contextlib.suppress(ValueError):
            sub.run_id = UUID(str(run))


async def _ending_if_over(sub: _ConversationSub) -> None:
    """The watched run went quiet: if it is over, say how it ended."""
    if sub.run_id is None:
        return
    run = await _run_now(sub.run_id)
    ending = run_ending_payload(run) if run is not None else None
    if ending is not None and sub.active:
        sub.run_id = None
        sub.connection.offer_events(sub.id, None, [dumps(ending)])


async def _still_allowed(sub: _ConversationSub) -> None:
    """Decide the subscription's access again; end it if it was lost. A
    database error is raised, so the clock decides again at its next check."""
    try:
        await _authorize(
            sub.connection.pod_id, sub.connection.user_id, sub.conversation_id, None
        )
    except LiveAccessDenied:
        sub.connection.offer_unsubscribed(
            sub.id, UnsubscribedReason.ACCESS_REVOKED.value
        )


async def _check_access(sub: _ConversationSub, access: AccessClock) -> None:
    try:
        await access.check(
            lambda: pod_grants_epoch(sub.connection.pod_id),
            lambda: _still_allowed(sub),
        )
    except (SQLAlchemyError, OSError) as exc:
        logger.warning(
            "agent.live.conversation_access_check_failed.degraded",
            conversation_id=str(sub.conversation_id),
            error_type=type(exc).__name__,
            exc_info=True,
        )


async def _forward(sub: _ConversationSub, listening: asyncio.Event) -> None:
    """Hand each of the conversation's events to the subscriber, until it goes
    or loses the right to them."""
    access = AccessClock(
        live_settings, check_seconds=live_settings.live_run_access_check_seconds
    )
    access.start(sub.epoch)
    try:
        async with conversation_events(str(sub.conversation_id)) as events:
            listening.set()
            stream = aiter(events)
            # The pull is held across a quiet interval, never cancelled:
            # cancelling an async generator's ``__anext__`` closes it.
            pending: asyncio.Future[JsonObject] | None = None
            quiet_until = time.monotonic() + IDLE_CHECK_SECONDS
            try:
                while sub.active:
                    if pending is None:
                        pending = asyncio.ensure_future(anext(stream))
                    wait = min(quiet_until, access.next_check) - time.monotonic()
                    done, _ = await asyncio.wait({pending}, timeout=max(wait, 0.0))
                    if time.monotonic() >= access.next_check:
                        await _check_access(sub, access)
                        if not sub.active:
                            return
                    if not done:
                        if time.monotonic() >= quiet_until:
                            quiet_until = time.monotonic() + IDLE_CHECK_SECONDS
                            await _ending_if_over(sub)
                        continue
                    quiet_until = time.monotonic() + IDLE_CHECK_SECONDS
                    pulled, pending = pending, None
                    try:
                        event = pulled.result()
                    except StopAsyncIteration:
                        return
                    if not sub.active:
                        return
                    _heard(sub, event)
                    sub.connection.offer_events(sub.id, None, [dumps(event)])
            finally:
                if pending is not None:
                    pending.cancel()
    except asyncio.CancelledError:
        raise
    except (RedisError, SQLAlchemyError, OSError, RuntimeError) as exc:
        # The run carries on and its messages are stored; the client hears it
        # missed some and refetches.
        logger.warning(
            "agent.live.conversation_stream_lost.degraded",
            conversation_id=str(sub.conversation_id),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        if sub.active:
            sub.connection.offer_resync(sub.id, None, ResyncReason.OVERFLOW)
    finally:
        listening.set()


class ConversationTopic:
    name = "conversation"

    async def subscribe(
        self, connection: LiveConnection, request: SubscribeRequest
    ) -> None:
        params = parse_params(_Params, request.params, request_id=request.id)
        # Read before access is decided: a grant moving after it is a move.
        epoch = await pod_grants_epoch(connection.pod_id)
        run = await _authorize(
            connection.pod_id,
            connection.user_id,
            params.conversation_id,
            params.agent_run_id,
        )
        ending = run_ending_payload(run) if run is not None else None
        sub = _ConversationSub(
            id=request.id,
            connection=connection,
            conversation_id=params.conversation_id,
            run_id=run.id if run is not None and ending is None else None,
            epoch=epoch,
        )
        connection.subscriptions[request.id] = sub
        listening = asyncio.Event()
        sub.task = create_background_task(
            _forward(sub, listening), name="live-conversation-forward"
        )
        # Listening before `subscribed`: a client that reads the conversation
        # once subscribed then misses nothing published after its read.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(listening.wait(), timeout=_LISTEN_TIMEOUT)
        connection.offer_subscribed(request.id, None)
        state: JsonObject = {
            "type": "run",
            "agent_run_id": str(run.id) if run is not None else None,
            "status": run.status.value if run is not None else None,
        }
        connection.offer_events(
            request.id, None, [dumps(state), *([dumps(ending)] if ending else [])]
        )
        if request.since is not None:
            connection.offer_resync(request.id, None, ResyncReason.NO_REPLAY)


def live_contribution() -> LiveContribution:
    return LiveContribution(
        topics=(ConversationTopic(),),
        streams={CONVERSATION_STREAM: conversation_events},
    )


__all__ = [
    "CONVERSATION_STREAM",
    "ConversationTopic",
    "conversation_events",
    "live_contribution",
]
