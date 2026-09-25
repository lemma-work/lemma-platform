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

from app.core.domain.realtime import RealtimeChannel, RealtimeSlowConsumerError
from app.core.log.log import get_logger
from app.core.origin import Origin, OriginKind, origin_scope
from app.modules.agent.domain.agent_host_link import InteractionWaitBody, McpBody
from app.modules.agent.domain.value_objects import JsonObject
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


class LinkUnauthorized(Exception):
    """The request's token does not grant access to its conversation."""


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
    ) -> None:
        self._service = service
        self._channels = channels
        self._recheck_seconds = recheck_seconds
        self._max_wait_seconds = max_wait_seconds

    async def _authorize(self, body: McpBody | InteractionWaitBody) -> None:
        if not await self._service.authorize(
            conversation_id=body.conversation_id, token=body.token
        ):
            raise LinkUnauthorized("the token does not grant this conversation")

    async def relay_request(self, body: McpBody) -> JsonObject:
        """Answer one ``tools/list`` or ``tools/call``, as the MCP result's JSON.

        A tool that fails comes back as an ``isError`` result, not an exception:
        ``call_tool`` owns that boundary, so the model sees the failure and
        carries on with its turn instead of losing its tools.
        """
        await self._authorize(body)
        with origin_scope(Origin(OriginKind.MCP_CONVERSATION)):
            result: mcp.types.ListToolsResult | mcp.types.CallToolResult
            if body.method == "tools/list":
                result = mcp.types.ListToolsResult(
                    tools=await self._service.list_tools(
                        conversation_id=body.conversation_id,
                        agent_run_id=body.run_id,
                    )
                )
            else:
                params = mcp.types.CallToolRequestParams.model_validate(body.params)
                result = await self._service.call_tool(
                    conversation_id=body.conversation_id,
                    agent_run_id=body.run_id,
                    name=params.name,
                    arguments=params.arguments or {},
                )
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def wait_for_interaction(self, body: InteractionWaitBody) -> JsonObject:
        """Hold until a person decides the parked call, then return the answer.

        Raises ``TimeoutError`` after the maximum wait; the host treats that as
        the interaction going unanswered, as it did when its own poll gave up.
        """
        await self._authorize(body)
        answer = await self._decided(body)
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
                answer = await self._decided(body)
                if answer is not None:
                    return answer
        raise TimeoutError("the interaction was not decided in time")

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
