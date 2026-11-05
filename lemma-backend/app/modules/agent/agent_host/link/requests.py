"""The answers to a linked host's request frames.

Split from ``session``, which owns the connection's lifecycle: reading frames,
supervising the tasks that answer them, and closing. This is what each request
means once a ``hello`` has said who is on the other end -- ``control``,
``events``, ``harnesses`` and ``revoke`` against the store, ``mcp`` and
``interaction_wait`` against the conversation's tools. One is built per link,
by the ``hello`` that opened it.
"""

from __future__ import annotations

from app.core.log.log import get_logger
from app.modules.agent.agent_host.control.repository_common import (
    AgentHostNotFound,
    AgentHostProtocolViolation,
    AgentHostRepositoryError,
)
from app.modules.agent.agent_host.domain.host import (
    AgentHostCapacity,
    AgentHostEventBatch,
    HostHello,
)
from app.modules.agent.agent_host.domain.link import (
    REVOKED_OR_MISSING_REASON,
    CommandsBody,
    ControlBody,
    ControlOkBody,
    EventsOkBody,
    HarnessesBody,
    HarnessesOkBody,
    InteractionOkBody,
    InteractionWaitBody,
    LinkCloseCode,
    LinkErrorCode,
    LinkFrame,
    McpBody,
    McpOkBody,
    ServerFrameType,
)
from app.modules.agent.agent_host.link.mcp_relay import AgentHostLinkMcp
from app.modules.agent.agent_host.link.outbound import OutboundStager, command_frames
from app.modules.agent.agent_host.link.store import AgentHostLinkStore, LinkedHost
from app.modules.agent.agent_host.link.wire import (
    TRANSIENT_ERRORS,
    EmptyBody,
    LinkClose,
    LinkRequestError,
    LinkWriter,
    OutboundFrameTooLarge,
    SentCommands,
    event_error,
    parse_control_items,
)
from app.modules.agent.agent_host.mcp_authorization import RelayingHost

logger = get_logger(__name__)


class LinkedHostRequests:
    """One linked host's request frames, each answered on the link's writer."""

    def __init__(
        self,
        *,
        host: LinkedHost,
        hello: HostHello,
        capacity: AgentHostCapacity,
        store: AgentHostLinkStore,
        mcp: AgentHostLinkMcp,
        writer: LinkWriter,
        sent: SentCommands,
        stager: OutboundStager,
    ) -> None:
        self.host = host
        self.hello = hello
        #: The host's free run slots, as its latest ``control`` reported them.
        self.capacity = capacity
        self._store = store
        self._mcp = mcp
        self._writer = writer
        self._sent = sent
        self._stager = stager

    async def control(self, frame: LinkFrame) -> None:
        host_id = self.host.host_id
        updates, refused = parse_control_items(ControlBody.model_validate(frame.body))
        try:
            commands = await self._store.apply_control(
                host_id=host_id, hello=self.hello, updates=updates
            )
        except AgentHostNotFound, AgentHostProtocolViolation:
            # The host was revoked, or its identity no longer matches the
            # credential, between frames.
            raise LinkClose(
                LinkCloseCode.REVOKED_OR_MISSING, REVOKED_OR_MISSING_REASON
            ) from None
        self.capacity = updates.capacity
        self._sent.acknowledged(updates.acknowledged_command_ids)
        fresh = self._sent.unsent(commands)
        try:
            frames = await command_frames(self._stager, fresh, host_id=host_id)
        except TRANSIENT_ERRORS:
            # The heartbeat is answered regardless: commands whose payload
            # could not be staged go out on a later push.
            self._sent.forget([command.command_id for command in fresh])
            logger.warning(
                "agent.agent_host_link.push_skipped.degraded",
                host_id=str(host_id),
                exc_info=True,
            )
            frames = []
        await self._writer.send_frame(
            ServerFrameType.CONTROL_OK,
            ControlOkBody(commands=frames[0] if frames else [], refused=refused),
            re=frame.id,
        )
        # What did not fit beside the answer follows it, a frame at a time.
        for more in frames[1:]:
            try:
                await self._writer.send_frame(
                    ServerFrameType.COMMANDS, CommandsBody(commands=more)
                )
            except OutboundFrameTooLarge:
                continue  # Logged by the writer; see ``LinkPusher``.

    async def events(self, frame: LinkFrame) -> None:
        batch = AgentHostEventBatch.model_validate(frame.body)
        try:
            ack = await self._store.append_events(
                host_id=self.host.host_id, batch=batch
            )
        except AgentHostRepositoryError as exc:
            raise event_error(exc) from None
        await self._writer.send_frame(
            ServerFrameType.EVENTS_OK, EventsOkBody(ack=ack), re=frame.id
        )

    async def harnesses(self, frame: LinkFrame) -> None:
        host_id = self.host.host_id
        body = HarnessesBody.model_validate(frame.body)
        for snapshot in body.harnesses:
            # An option the agent added that this build cannot offer. Named by
            # its type, never by its content; the rest of the harness stands.
            if snapshot.skipped_config_options:
                logger.info(
                    "agent.agent_host_link.config_options_skipped",
                    host_id=str(host_id),
                    harness_key=snapshot.harness_key,
                    skipped=sorted(set(snapshot.skipped_config_options)),
                )
        try:
            items = await self._store.publish_harnesses(
                host_id=host_id, snapshots=body.harnesses
            )
        except AgentHostRepositoryError as exc:
            raise LinkRequestError(LinkErrorCode.INVALID_FRAME, str(exc)) from None
        await self._writer.send_frame(
            ServerFrameType.HARNESSES_OK, HarnessesOkBody(items=items), re=frame.id
        )

    async def revoke(self, frame: LinkFrame) -> None:
        try:
            await self._store.revoke_host(
                host_id=self.host.host_id, user_id=self.host.user_id
            )
        except AgentHostNotFound:
            raise LinkClose(
                LinkCloseCode.REVOKED_OR_MISSING, REVOKED_OR_MISSING_REASON
            ) from None
        await self._writer.send_frame(ServerFrameType.REVOKED, EmptyBody(), re=frame.id)
        raise LinkClose(LinkCloseCode.NORMAL, "revoked")

    def _relaying_host(self) -> RelayingHost:
        return RelayingHost(host_id=self.host.host_id, user_id=self.host.user_id)

    async def mcp_request(self, frame: LinkFrame) -> None:
        result = await self._mcp.relay_request(
            McpBody.model_validate(frame.body), host=self._relaying_host()
        )
        result = await self._stager.tool_result(result)
        await self._writer.send_frame(
            ServerFrameType.MCP_OK, McpOkBody(result=result), re=frame.id
        )

    async def interaction_wait(self, frame: LinkFrame) -> None:
        body = InteractionWaitBody.model_validate(frame.body)
        try:
            answer = await self._mcp.wait_for_interaction(
                body, host=self._relaying_host()
            )
        except TimeoutError:
            raise LinkRequestError(
                LinkErrorCode.UNAVAILABLE,
                "the interaction was not decided in time",
                retryable=True,
            ) from None
        await self._writer.send_frame(
            ServerFrameType.INTERACTION_OK,
            InteractionOkBody(answer=answer),
            re=frame.id,
        )
