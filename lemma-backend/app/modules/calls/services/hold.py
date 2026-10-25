"""Holding one call's socket: who may, the voice it opens, and the session it runs."""

from __future__ import annotations

import asyncio
import contextlib
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect, status
from sqlalchemy.exc import SQLAlchemyError

from app.core.domain.errors import DomainError
from app.core.live.pod_access import close_code_for, require_pod_access
from app.core.live.protocol import CLOSE_NOT_FOUND
from app.core.log.log import get_logger
from app.modules.agent.contracts.calls import owned_call, retire_empty_call
from app.modules.calls.config import VoiceEngineName, calls_settings
from app.modules.calls.domain.frames import decode
from app.modules.calls.domain.party import CallParty
from app.modules.calls.domain.voice import INPUT_SAMPLE_RATE, EngineUnavailable
from app.modules.calls.domain.voice_brief import voice_brief
from app.modules.calls.services.agents import CallAgents, Party, as_caller
from app.modules.calls.services.engines import build_engine, engines_offered
from app.modules.calls.services.gate import Caller, route_turn
from app.modules.calls.services.session import CallLimits, CallSession
from app.modules.calls.services.slots import call_slot

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
    background = str(start.get("context") or "") or None
    agents = CallAgents(call_id=call_id, party=party)
    instructions = voice_brief(
        teammate=str(start.get("teammate") or "your teammate")[:120],
        pod=str(start.get("pod") or "this pod")[:120],
        context=background,
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
            max_seconds=calls_settings.call_max_minutes * 60.0,
            quiet_window_ms=calls_settings.call_quiet_window_ms,
        ),
        background=background,
    )
    logger.info(
        "calls.socket.started.observed", call_id=str(call_id), engine=engine_name
    )
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
        await engine.close()
        await agents.close()
        transcript = call.transcript()
        if transcript:
            await agents.record("Call transcript\n\n" + transcript)
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
