"""Holding one call's socket: who may, the voice it opens, and the session it runs."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect, status
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from websockets.exceptions import WebSocketException

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.pod_access import close_code_for, require_pod_access
from app.core.live.protocol import CLOSE_NOT_FOUND
from app.core.log.log import get_logger
from app.modules.agent.contracts.calls import (
    held_call,
    owned_call,
    retire_empty_call,
    set_call_live,
)
from app.modules.calls.config import VoiceEngineName, calls_settings
from app.modules.calls.domain.frames import decode
from app.modules.calls.domain.party import CallParty
from app.modules.calls.domain.voice import (
    INPUT_SAMPLE_RATE,
    EngineUnavailable,
    VoiceEngine,
)
from app.modules.calls.domain.voice_brief import voice_brief
from app.modules.calls.services.agents import CallAgents, Party, as_caller
from app.modules.calls.services.engines import build_engine, engines_offered
from app.modules.calls.services.gate import Caller, route_turn
from app.modules.calls.services.lease import CallLease
from app.modules.calls.services.session import CallLimits, CallSession
from app.modules.calls.services.slots import call_slot
from app.modules.usage.contracts.metering import check_run_budget

logger = get_logger(__name__)

_START_TIMEOUT_SECONDS = 15.0
_MAX_START_CHARS = 20_000
CLOSE_UNAVAILABLE = 4503
CLOSE_TOO_MANY_CALLS = 4429


async def hold_call(
    websocket: WebSocket, *, pod_id: UUID, call_id: UUID, user_id: UUID
) -> None:
    """Authorize the caller for this call, then hold it until it ends."""
    party = Party(user_id=user_id, pod_id=pod_id)
    try:
        await require_pod_access(pod_id, party.user_id)
        async with as_caller(party) as scope:
            mine = await owned_call(
                scope.uow, conversation_id=call_id, user_id=party.user_id, pod_id=pod_id
            )
            organization_id = scope.organization_id
    except DomainError as exc:
        await websocket.close(code=close_code_for(exc.status_code), reason=exc.message)
        return
    if not mine:
        await websocket.close(code=CLOSE_NOT_FOUND, reason="Call not found.")
        return
    async with call_slot(party.user_id, limit=calls_settings.calls_per_user) as held:
        if not held:
            await websocket.close(
                code=CLOSE_TOO_MANY_CALLS,
                reason=f"At most {calls_settings.calls_per_user} calls at once.",
            )
            return
        await _hold_call(
            websocket, call_id=call_id, party=party, organization_id=organization_id
        )
        # After a normal end only. A call that raised -- its transcript not
        # saved -- may still have a turn on its way to the agents, and
        # archiving it under that turn would hide a call something happened in.
        await _retire_if_empty(party, call_id)


async def _hold_call(
    websocket: WebSocket, *, call_id: UUID, party: Party, organization_id: UUID | None
) -> None:
    socket = _Socket(websocket)
    start = await _start_frame(socket)
    if start is None:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Expected start."
        )
        return
    engine_name = _engine_from(start.get("engine"))
    if engine_name is None:
        await websocket.close(
            code=CLOSE_UNAVAILABLE, reason="That voice engine is not set up."
        )
        return
    # Taken first: a connection this one replaces writes its transcript as it
    # ends, and what was said on the call is read only after that.
    lease = CallLease(call_id)
    await lease.take()
    try:
        await _held(
            websocket,
            socket,
            start,
            engine_name=engine_name,
            call_id=call_id,
            party=party,
            organization_id=organization_id,
            lease=lease,
        )
    finally:
        await lease.release()


async def _held(
    websocket: WebSocket,
    socket: _Socket,
    start: dict[str, object],
    *,
    engine_name: VoiceEngineName,
    call_id: UUID,
    party: Party,
    organization_id: UUID | None,
    lease: CallLease,
) -> None:
    held_before = await _held_seconds(party, call_id)
    left = calls_settings.call_max_minutes * 60.0 - held_before
    if left <= 0:
        await websocket.close(
            code=CLOSE_TOO_MANY_CALLS, reason="This call reached its time limit."
        )
        return
    budget = _budget(party, organization_id)
    try:
        await budget()
    except DomainError as exc:
        await websocket.close(code=CLOSE_UNAVAILABLE, reason=_reason(exc))
        return
    agents = CallAgents(call_id=call_id, party=party)
    instructions = voice_brief(
        teammate=str(start.get("teammate") or "your teammate")[:120],
        pod=str(start.get("pod") or "this pod")[:120],
        context=str(start.get("context") or "") or None,
        earlier=await _earlier(agents),
    )
    talk_party = CallParty(
        user_id=party.user_id,
        organization_id=organization_id,
        pod_id=party.pod_id,
        conversation_id=call_id,
    )
    try:
        engine = await build_engine(
            engine_name,
            settings=calls_settings,
            instructions=instructions,
            party=talk_party,
        )
    except (EngineUnavailable, DomainError) as exc:
        await websocket.close(code=CLOSE_UNAVAILABLE, reason=_reason(exc))
        return
    try:
        await engine.open()
    except EngineUnavailable as exc:
        # Partly open is still open: a socket or task it started is released.
        await engine.close()
        await websocket.close(code=CLOSE_UNAVAILABLE, reason=_reason(exc))
        return
    caller = Caller(
        user_id=party.user_id,
        organization_id=organization_id,
        pod_id=party.pod_id,
        call_id=call_id,
    )
    call = CallSession(
        socket=socket,
        engine=engine,
        agents=agents,
        gate=lambda turn, recent: route_turn(turn, recent, caller),
        limits=CallLimits(
            max_seconds=left, quiet_window_ms=calls_settings.call_quiet_window_ms
        ),
        holding=lease,
        check_budget=budget,
        muted=bool(start.get("muted")),
    )
    await _set_live(party, call_id, live=True)
    logger.info(
        "calls.socket.started.observed", call_id=str(call_id), engine=engine_name
    )
    began = time.monotonic()
    try:
        await call.send(
            {
                "type": "ready",
                "call_id": str(call_id),
                "engine": engine_name,
                "sample_rate": engine.output_sample_rate,
            }
        )
        await call.run()
    finally:
        await _wind_down(call, engine, agents)
        await _set_live(party, call_id, live=False, held_for=time.monotonic() - began)
        logger.info(
            "calls.socket.ended.observed",
            call_id=str(call_id),
            reason=call.end_reason,
            caller_audio_seconds=round(
                call.stats.audio_in_bytes / (INPUT_SAMPLE_RATE * 2), 1
            ),
            caller_turns=call.stats.caller_turns,
            voice_lines=call.stats.voice_lines,
        )
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close()


async def _wind_down(
    call: CallSession, engine: VoiceEngine, agents: CallAgents
) -> None:
    """Every step runs whatever the one before it did: an engine that fails to
    close must not leak the call's watchers or lose its transcript."""
    steps: tuple[tuple[Callable[[], Awaitable[None]], str], ...] = (
        (lambda: call.flush(final=True), "transcript"),
        (engine.close, "engine"),
        (agents.close, "agents"),
    )
    for step, name in steps:
        try:
            # Created here, one at a time: a step not reached is never left
            # as an unawaited coroutine.
            await step()
        except (
            DomainError,
            SQLAlchemyError,
            RedisError,
            OSError,
            RuntimeError,
            WebSocketException,
        ):
            logger.warning("calls.socket.wind_down.failed", step=name, exc_info=True)


def _budget(
    party: Party, organization_id: UUID | None
) -> Callable[[], Awaitable[None]]:
    """The person's spend, checked as a call starts and again as it goes: a
    realtime voice runs on the deployment's keys, like a system model."""

    async def check() -> None:
        try:
            await check_run_budget(
                factory=SessionUnitOfWorkFactory(async_session_maker),
                organization_id=organization_id,
                user_id=party.user_id,
                profile_scope="SYSTEM",
            )
        except SQLAlchemyError, OSError:
            # The ledger being unreachable is not a reason to end a call.
            logger.warning("calls.socket.budget.unavailable", exc_info=True)

    return check


async def _held_seconds(party: Party, call_id: UUID) -> float:
    try:
        async with as_caller(party) as scope:
            mark = await held_call(scope.uow, call_id)
    except DomainError, SQLAlchemyError, OSError:
        logger.warning("calls.socket.held.failed", exc_info=True)
        return 0.0
    return mark.held_seconds if mark is not None else 0.0


async def _set_live(
    party: Party, call_id: UUID, *, live: bool, held_for: float = 0.0
) -> None:
    """While live, the coordinator has the call's brief and its voice tool;
    once not, the chat reads as itself again."""
    try:
        async with as_caller(party) as scope:
            await set_call_live(scope.uow, call_id, live=live, held_for=held_for)
    except DomainError, SQLAlchemyError, OSError:
        logger.warning("calls.socket.live.failed", live=live, exc_info=True)


#: A close frame carries 125 bytes, two of them the code.
_MAX_REASON_BYTES = 123


def _reason(exc: EngineUnavailable | DomainError) -> str:
    text = exc.message if isinstance(exc, DomainError) else str(exc)
    # Cut by bytes, dropping a character split at the edge: a longer reason
    # makes the close itself fail, and the caller sees a dropped call.
    return text.encode()[:_MAX_REASON_BYTES].decode(errors="ignore")


async def _earlier(agents: CallAgents) -> str | None:
    """What was said on this call before, when it is being resumed."""
    try:
        earlier = await agents.earlier()
    except DomainError, SQLAlchemyError, OSError:
        # A resumed call that cannot read its past is still a call; the voice
        # just starts without it.
        logger.warning("calls.socket.earlier.failed", exc_info=True)
        return None
    return "\n\n".join(earlier) or None


async def _retire_if_empty(party: Party, call_id: UUID) -> None:
    """Take a call that nothing was said in out of the chat list."""
    try:
        async with as_caller(party) as scope:
            await retire_empty_call(scope.uow, call_id)
    except DomainError, SQLAlchemyError, OSError:
        logger.warning(
            "calls.socket.retire.failed", call_id=str(call_id), exc_info=True
        )


def _engine_from(raw: object) -> VoiceEngineName | None:
    engines = engines_offered(calls_settings)
    if raw is None or raw == "":
        return engines[0] if engines else None
    return raw if raw in engines else None  # type: ignore[return-value]


async def _start_frame(socket: _Socket) -> dict[str, object] | None:
    try:
        async with asyncio.timeout(_START_TIMEOUT_SECONDS):
            message = await socket.receive_message()
    except TimeoutError:
        return None
    if not isinstance(message, str) or len(message) > _MAX_START_CHARS:
        return None
    try:
        frame = decode(message)
    except ValueError:
        return None
    return frame if isinstance(frame, dict) and frame.get("type") == "start" else None


class _Socket:
    """The caller's WebSocket, as the session sees it."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_bytes(self, data: bytes) -> None:
        await self._websocket.send_bytes(data)

    async def send_text(self, data: str) -> None:
        await self._websocket.send_text(data)

    async def receive_message(self) -> bytes | str | None:
        try:
            message = await self._websocket.receive()
        except WebSocketDisconnect, RuntimeError:
            return None
        if message.get("type") == "websocket.disconnect":
            return None
        data = message.get("bytes")
        if isinstance(data, bytes):
            return data
        text = message.get("text")
        return text if isinstance(text, str) else None
