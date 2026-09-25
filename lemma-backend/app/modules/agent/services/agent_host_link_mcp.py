"""Lemma MCP over the Agent Host link: tool calls and parked-interaction waits.

The agent on a user's machine reaches Lemma's tools through the host's MCP
bridge. That bridge used to speak streamable HTTP to a conversation MCP mount,
and poll a second route every two seconds while an ``ask_user`` or
``request_approval`` was parked. Both now travel on the link the host already
holds, as ``mcp`` and ``interaction_wait`` frames.

What did not change is authorization. Every request carries the run's own Lemma
token and the conversation it acts in, and is re-authorized against them on
every call exactly as the HTTP mount did: the host being authenticated says
which machine is talking, not which conversation it may touch.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import mcp.types
from redis.exceptions import RedisError
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.domain.realtime import RealtimeChannel, RealtimeSlowConsumerError
from app.core.log.log import get_logger
from app.core.origin import Origin, OriginKind, origin_scope
from app.modules.agent.domain.agent_host_link import (
    InteractionWaitBody,
    LinkErrorCode,
    McpBody,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.services.agent_host_link_tool_calls import (
    DispatchedCallFailed,
    ToolCallLedger,
    ToolCallOutcome,
    keep_executing,
    report_if_orphaned,
    tool_call_key,
)
from app.modules.agent.services.conversation_mcp_service import (
    ConversationMCPService,
)
from app.modules.agent.services.realtime import conversation_channel


logger = get_logger(__name__)

#: The floor under the push that wakes a parked wait. The push is the fast
#: path; this is what a lost message costs at most.
INTERACTION_RECHECK_SECONDS = 10.0

#: How long one ``interaction_wait`` may be held. Matches the 30 minutes the
#: host holds an ACP permission request open, after which the agent has given
#: up on the answer anyway.
INTERACTION_MAX_WAIT_SECONDS = 30 * 60

#: Failures that mean "Lemma could not reach something", as opposed to a bug.
#: The same set the link answers UNAVAILABLE for; kept here too because the
#: wire module imports this one.
_TRANSIENT = (RedisError, OperationalError, InterfaceError, PoolTimeoutError, OSError)

_UNFINISHED = (
    "Lemma could not finish this tool call. It may have run; it was not repeated."
)


class LinkUnauthorized(Exception):
    """The request's token does not grant access to its conversation."""


class InteractionRunEnded(Exception):
    """The run a parked interaction belongs to has ended; nobody will resume it."""


class NoticeStream:
    """Wait on a subscription with a timeout, without cancelling the iterator.

    ``asyncio.wait_for`` cancels the awaitable it times out, and cancelling an
    ``anext()`` closes the async generator behind it -- so the *second* timed
    wait raises ``StopAsyncIteration``. That exact bug once took every host
    OFFLINE five seconds after it connected. The pending read here outlives a
    timeout and is picked up by the next call instead.
    """

    def __init__(self, messages: AsyncIterator[str | bytes] | None) -> None:
        self._messages = messages
        self._pending: asyncio.Future[str | bytes] | None = None

    async def next_notice(self, timeout: float) -> str | bytes | None:
        """The next message, or ``None`` if ``timeout`` passed first.

        With no subscription it simply sleeps: the caller's floor timer is then
        the only clock, which is the degraded mode, not a failure.
        """
        if self._messages is None:
            await asyncio.sleep(timeout)
            return None
        if self._pending is None:
            self._pending = asyncio.ensure_future(anext(self._messages))
        done, _ = await asyncio.wait({self._pending}, timeout=timeout)
        if not done:
            return None
        finished, self._pending = self._pending, None
        return finished.result()

    def detach(self) -> None:
        """Stop listening and fall back to the timer alone."""
        self.close()
        self._messages = None

    def close(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None


@asynccontextmanager
async def notice_stream(
    channels: RealtimeChannel, channel: str, *, host_id: str | None = None
) -> AsyncIterator[NoticeStream]:
    """Subscribe to ``channel``, degrading to a timer-only stream if Redis is down."""
    try:
        subscription = channels.subscribe([channel])
        messages = await subscription.__aenter__()
    except RedisError, OSError, RuntimeError:
        logger.warning(
            "agent.agent_host_link.subscription_unavailable.degraded",
            host_id=host_id,
            exc_info=True,
        )
        stream = NoticeStream(None)
        yield stream
        return
    stream = NoticeStream(messages)
    try:
        yield stream
    finally:
        stream.close()
        with contextlib.suppress(asyncio.CancelledError):
            await subscription.__aexit__(None, None, None)


def _names_tool_call(raw: str | bytes, tool_call_id: str) -> bool:
    """Whether a conversation frame is about this tool call.

    Deciding an interaction writes a synthesized tool RETURN under the parked
    call's id and publishes it on the conversation's channel, which is how an
    open chat shows the answer. That publish is the push; nothing new had to be
    added to any of the paths that decide an interaction.
    """
    try:
        frame = json.loads(raw)
    except ValueError:
        return False
    data = frame.get("data") if isinstance(frame, dict) else None
    return isinstance(data, dict) and data.get("tool_call_id") == tool_call_id


class AgentHostLinkMcp:
    def __init__(
        self,
        service: ConversationMCPService,
        channels: RealtimeChannel,
        *,
        recheck_seconds: float = INTERACTION_RECHECK_SECONDS,
        max_wait_seconds: float = INTERACTION_MAX_WAIT_SECONDS,
        ledger: ToolCallLedger | None = None,
    ) -> None:
        self._service = service
        self._channels = channels
        self._recheck_seconds = recheck_seconds
        self._max_wait_seconds = max_wait_seconds
        self._ledger = ledger or ToolCallLedger()

    async def _authorize(self, body: McpBody | InteractionWaitBody) -> None:
        if not await self._service.authorize(
            conversation_id=body.conversation_id,
            token=body.token,
            agent_run_id=body.run_id,
        ):
            raise LinkUnauthorized("the token does not grant this conversation")

    async def relay_request(self, body: McpBody) -> JsonObject:
        """Answer one ``tools/list`` or ``tools/call``, as the MCP result's JSON.

        A tool that fails comes back as an ``isError`` result, not an exception:
        ``call_tool`` owns that boundary, so the model sees the failure and
        carries on with its turn instead of losing its tools.

        Everything before a ``tools/call`` is dispatched -- authorizing, taking
        the call's claim -- may fail retryably. Anything after raises
        ``DispatchedCallFailed``, which is never retryable. A call with a
        ``request_id`` executes at most once; see ``agent_host_link_tool_calls``.
        """
        await self._authorize(body)
        if body.method == "tools/list":
            with origin_scope(Origin(OriginKind.MCP_CONVERSATION)):
                listed = mcp.types.ListToolsResult(
                    tools=await self._service.list_tools(
                        conversation_id=body.conversation_id,
                        agent_run_id=body.run_id,
                    )
                )
            return listed.model_dump(mode="json", by_alias=True, exclude_none=True)
        if body.run_id is None or body.request_id is None:
            # A host too old to name its calls: executed as it arrives, and a
            # link that drops takes the call with it, as it always did.
            return await self._dispatched(body)
        key = tool_call_key(body.run_id, body.request_id)
        if not await self._ledger.claim_call(key):
            return self._answer_from(await self._ledger.wait_for_outcome(key))
        execution = asyncio.ensure_future(self._execute_and_record(body, key))
        keep_executing(execution)
        # Shielded: the link closing cancels this await, never the call.
        try:
            return await asyncio.shield(execution)
        except asyncio.CancelledError:
            report_if_orphaned(execution)
            raise

    async def _call(self, body: McpBody) -> JsonObject:
        params = mcp.types.CallToolRequestParams.model_validate(body.params)
        with origin_scope(Origin(OriginKind.MCP_CONVERSATION)):
            result = await self._service.call_tool(
                conversation_id=body.conversation_id,
                agent_run_id=body.run_id,
                name=params.name,
                arguments=params.arguments or {},
            )
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def _dispatched(self, body: McpBody) -> JsonObject:
        """Run the call; an outage from here on is final (not retryable).

        A bug propagates as itself: the link answers it INTERNAL, logged in
        full, and not retryable for a ``tools/call`` (``answer_request``).
        """
        try:
            return await self._call(body)
        except _TRANSIENT as exc:
            logger.warning(
                "agent.agent_host_link.tool_call_unavailable.degraded",
                conversation_id=str(body.conversation_id),
                exc_info=exc,
            )
            raise DispatchedCallFailed(LinkErrorCode.UNAVAILABLE, _UNFINISHED) from exc

    async def _execute_and_record(self, body: McpBody, key: str) -> JsonObject:
        # Failed unless it finished: a bug, or this process shutting down,
        # leaves it unknown whether the tool acted, and a retry is told so
        # rather than run.
        outcome = ToolCallOutcome(
            state="failed", code=LinkErrorCode.INTERNAL.value, message=_UNFINISHED
        )
        try:
            result = await self._dispatched(body)
            outcome = ToolCallOutcome(state="done", result=result)
            return result
        except DispatchedCallFailed as failed:
            outcome = ToolCallOutcome(
                state="failed", code=failed.code.value, message=failed.message
            )
            raise
        finally:
            await asyncio.shield(self._record(key, outcome))

    async def _record(self, key: str, outcome: ToolCallOutcome) -> None:
        try:
            await self._ledger.record_outcome(key, outcome)
        except _TRANSIENT:
            # The caller on this link still gets its answer; only a retry of
            # the same call would find the claim and not the outcome, and it
            # is refused rather than repeated.
            logger.warning(
                "agent.agent_host_link.tool_call_unrecorded.degraded",
                exc_info=True,
            )

    @staticmethod
    def _answer_from(outcome: ToolCallOutcome | None) -> JsonObject:
        if (
            outcome is not None
            and outcome.state == "done"
            and outcome.result is not None
        ):
            return outcome.result
        code = LinkErrorCode.INTERNAL
        if outcome is not None and outcome.code == LinkErrorCode.UNAVAILABLE.value:
            code = LinkErrorCode.UNAVAILABLE
        raise DispatchedCallFailed(
            code, outcome.message if outcome and outcome.message else _UNFINISHED
        )

    async def wait_for_interaction(self, body: InteractionWaitBody) -> JsonObject:
        """Hold until a person decides the parked call, then return the answer.

        Raises ``TimeoutError`` after the maximum wait; the host treats that as
        the interaction going unanswered, as it did when its own poll gave up.
        """
        await self._authorize(body)
        answer = await self._decided_while_running(body)
        if answer is not None:
            return answer
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._max_wait_seconds
        async with notice_stream(
            self._channels, conversation_channel(body.conversation_id)
        ) as notices:
            while (remaining := deadline - loop.time()) > 0:
                await self._wait_for_news(
                    notices, body, min(self._recheck_seconds, remaining)
                )
                answer = await self._decided_while_running(body)
                if answer is not None:
                    return answer
        raise TimeoutError("the interaction was not decided in time")

    async def _decided_while_running(
        self, body: InteractionWaitBody
    ) -> JsonObject | None:
        """The answer, if decided; raises once the run itself has ended.

        A wait outlives nothing: a stopped or failed run is never resumed by
        this answer, so holding the host's slot for it is waste.
        """
        answer = await self._decided(body)
        if answer is not None:
            return answer
        if body.run_id is not None and await self._service.run_has_ended(
            agent_run_id=body.run_id
        ):
            raise InteractionRunEnded("the run this interaction belongs to has ended")
        return None

    async def _wait_for_news(
        self, notices: NoticeStream, body: InteractionWaitBody, timeout: float
    ) -> None:
        """Drain the conversation's frames until one names this call or time runs out.

        A conversation mid-turn publishes a frame per streamed token, so most of
        what arrives here is somebody else's news; only a frame for this call is
        worth a query. Returning because the floor timer fired re-checks anyway.
        """
        loop = asyncio.get_running_loop()
        until = loop.time() + timeout
        while (left := until - loop.time()) > 0:
            try:
                raw = await notices.next_notice(left)
            except StopAsyncIteration, RealtimeSlowConsumerError:
                # The subscription ended or fell behind. Keep the wait alive on
                # the floor timer alone rather than failing a person's answer.
                logger.warning(
                    "agent.agent_host_link.interaction_push_lost.degraded",
                    conversation_id=str(body.conversation_id),
                    tool_call_id=body.tool_call_id,
                )
                notices.detach()
                return
            if raw is None or _names_tool_call(raw, body.tool_call_id):
                return

    async def _decided(self, body: InteractionWaitBody) -> JsonObject | None:
        return await self._service.parked_tool_return(
            conversation_id=body.conversation_id,
            tool_call_id=body.tool_call_id,
        )
