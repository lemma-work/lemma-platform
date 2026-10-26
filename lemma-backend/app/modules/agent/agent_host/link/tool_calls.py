"""A relayed ``tools/call`` executes at most once.

The host's MCP bridge sends each ``tools/call`` with a ``request_id`` it mints
once and reuses on every retry of that call, including on a new link after a
reconnect. ``(run_id, request_id)`` is the call's identity here:

* the first arrival claims it in Redis (``SET NX``) and executes it;
* an arrival while it runs waits for the recorded outcome instead of running
  it again;
* an arrival after it finished is answered from the record.

The record outlives the link that carried the call: a link that drops mid-call
does not cancel it (the caller holds a strong reference to the execution, not
the session), so the retry on the next link finds the outcome waiting.

The arrival executing a call keeps a heartbeat beside the claim. A replica that
dies hard mid-call (OOM, SIGKILL) leaves the claim saying "running" with nobody
behind it; a duplicate that finds the heartbeat gone is told at once that the
call may have run, instead of polling for half an hour.

Redis, not memory: the retry can land on another API replica.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent.agent_host.domain.link import LinkErrorCode
from app.modules.agent.domain.value_objects import JsonObject

logger = get_logger(__name__)

#: How long a finished call's outcome is kept: the window a host's retry of
#: the same call arrives in, which is minutes. A running claim is re-armed by
#: its heartbeat, so a long call never outlives its own record.
TOOL_CALL_RECORD_TTL_SECONDS = 10 * 60

#: The longest a duplicate waits for a live arrival that is executing the call.
TOOL_CALL_WAIT_SECONDS = 30 * 60

#: How often the executing arrival says it is alive, and how long a heartbeat
#: counts for. Three beats, so one slow Redis round trip is not a death.
TOOL_CALL_HEARTBEAT_SECONDS = 10.0
TOOL_CALL_HEARTBEAT_TTL_SECONDS = 30

#: The largest result kept for a retry. A tool result can carry images and run
#: to megabytes, and one stored per call on a shared Redis is not free; past
#: this the retry is told the call finished but its result was not kept.
MAX_RECORDED_RESULT_BYTES = 256 * 1024

#: Which process holds a claim, for whoever finds it.
_REPLICA = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"

_RESULT_NOT_KEPT = (
    "This call finished, but its result was too large to keep for a retry; "
    "it was not repeated."
)
_OWNER_GONE = (
    "Lemma lost track of this tool call while it ran. It may have run; it was "
    "not repeated."
)

#: How often a waiting duplicate re-reads the record, at first and at most.
_POLL_FIRST_SECONDS = 0.2
_POLL_MAX_SECONDS = 2.0


class DispatchedCallFailed(Exception):
    """A ``tools/call`` that failed once it had been dispatched.

    Answered with ``retryable=False`` whatever the cause: the tool may already
    have acted, so sending it again could act twice.
    """

    def __init__(self, code: LinkErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolCallOutcome(BaseModel):
    """What the record of one call says. ``running`` until it finishes."""

    state: Literal["running", "done", "failed"]
    #: The MCP result's JSON, for ``done`` -- absent when it was too large to
    #: keep, with ``message`` saying so.
    result: JsonObject | None = None
    #: A ``LinkErrorCode`` and a sentence, for ``failed``.
    code: str | None = None
    message: str = ""
    #: The process executing a ``running`` call.
    owner: str | None = None


def tool_call_key(run_id: UUID, request_id: str) -> str:
    return f"agent-host:tool-call:{run_id}:{request_id}"


def _heartbeat_key(key: str) -> str:
    return f"{key}:alive"


def kept_for_retry(outcome: ToolCallOutcome) -> ToolCallOutcome:
    """The outcome as the record keeps it: a result past the cap is dropped."""
    if outcome.result is None:
        return outcome
    size = len(json.dumps(outcome.result, separators=(",", ":"), default=str))
    if size <= MAX_RECORDED_RESULT_BYTES:
        return outcome
    return ToolCallOutcome(state="done", message=_RESULT_NOT_KEPT)


class ToolCallLedger:
    """The Redis record of each ``(run_id, request_id)`` call."""

    def __init__(
        self,
        redis: Redis | None = None,
        *,
        ttl_seconds: int = TOOL_CALL_RECORD_TTL_SECONDS,
        wait_seconds: float = TOOL_CALL_WAIT_SECONDS,
        heartbeat_seconds: float = TOOL_CALL_HEARTBEAT_SECONDS,
    ) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._wait_seconds = wait_seconds
        self._heartbeat_seconds = heartbeat_seconds

    def _client(self) -> Redis:
        if self._redis is None:
            self._redis = get_redis(url=settings.redis_url)
        return self._redis

    async def claim_call(self, key: str) -> bool:
        """Take the call for this arrival; False when another already has it."""
        running = ToolCallOutcome(state="running", owner=_REPLICA).model_dump_json()
        claimed = bool(
            await self._client().set(key, running, nx=True, ex=self._ttl_seconds)
        )
        if claimed:
            await self._beat(key)
        return claimed

    async def keep_alive(self, key: str) -> None:
        """Say this arrival is still executing ``key``, until cancelled."""
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                await self._beat(key)
                await self._client().expire(key, self._ttl_seconds)
            except RedisError:
                # A missed beat or two is what the heartbeat's TTL allows for;
                # a run of them reads to a duplicate as this replica gone.
                logger.warning(
                    "agent.agent_host_link.tool_call_heartbeat.degraded",
                    exc_info=True,
                )

    async def _beat(self, key: str) -> None:
        await self._client().set(
            _heartbeat_key(key), _REPLICA, ex=TOOL_CALL_HEARTBEAT_TTL_SECONDS
        )

    async def record_outcome(self, key: str, outcome: ToolCallOutcome) -> None:
        await self._client().set(
            key, kept_for_retry(outcome).model_dump_json(), ex=self._ttl_seconds
        )
        await self._client().delete(_heartbeat_key(key))

    async def read_outcome(self, key: str) -> ToolCallOutcome | None:
        raw = await self._client().get(key)
        if raw is None:
            return None
        try:
            return ToolCallOutcome.model_validate_json(raw)
        except ValidationError:
            return None

    async def wait_for_outcome(self, key: str) -> ToolCallOutcome | None:
        """The call's finished outcome, once the arrival executing it records it.

        None when the record vanished or the wait ran out: the call may or may
        not have run, and it must not run again. A ``failed`` outcome as soon
        as the arrival executing it has stopped beating: whatever process held
        the claim is gone, and nothing will ever record how the call ended.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._wait_seconds
        pause = _POLL_FIRST_SECONDS
        while True:
            outcome = await self.read_outcome(key)
            if outcome is None or outcome.state != "running":
                return outcome
            if not await self._client().exists(_heartbeat_key(key)):
                return ToolCallOutcome(
                    state="failed",
                    code=LinkErrorCode.INTERNAL.value,
                    message=_OWNER_GONE,
                )
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(pause, remaining))
            pause = min(pause * 2, _POLL_MAX_SECONDS)


#: Calls executing for some link, held here so that neither link teardown nor
#: garbage collection can end them: a task nothing references can be collected
#: mid-flight, and the session's own task set is cancelled when it closes.
_EXECUTING: set[asyncio.Task[JsonObject]] = set()  # memory: bounded -- in flight only


#: Failures already reported where they were raised.
_REPORTED: tuple[type[BaseException], ...] = (DispatchedCallFailed,)


def keep_executing(task: asyncio.Task[JsonObject]) -> None:
    _EXECUTING.add(task)
    task.add_done_callback(_finished)


def _finished(task: asyncio.Task[JsonObject]) -> None:
    _EXECUTING.discard(task)
    # Retrieved so asyncio does not report it as never retrieved: whoever
    # awaited it reported it, or ``report_if_orphaned`` will.
    if not task.cancelled():
        task.exception()


def report_if_orphaned(task: asyncio.Task[JsonObject]) -> None:
    """Log the failure of an execution whose link went away while it ran.

    With the link gone nothing else would: the request's own error path went
    with it. The outcome is recorded for the retry either way.
    """

    def report(done: asyncio.Task[JsonObject]) -> None:
        if done.cancelled():
            return
        failure = done.exception()
        if failure is not None and not isinstance(failure, _REPORTED):
            logger.error("agent.agent_host_link.tool_call.failed", exc_info=failure)

    task.add_done_callback(report)


__all__ = [
    "DispatchedCallFailed",
    "MAX_RECORDED_RESULT_BYTES",
    "TOOL_CALL_RECORD_TTL_SECONDS",
    "TOOL_CALL_WAIT_SECONDS",
    "ToolCallLedger",
    "ToolCallOutcome",
    "keep_executing",
    "report_if_orphaned",
    "tool_call_key",
]
