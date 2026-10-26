"""What Lemma sends a host, made to fit the link's 1 MiB message ceiling.

The host reads frames Lemma sends with no ceiling of its own, but the link is
one websocket for every run on the machine: a frame of several megabytes holds
up each heartbeat answer and op queued behind it, and the API holds every
socket to ``WS_MAX_BYTES`` in the other direction already. So nothing Lemma
sends passes it either (docs/architecture/agent-host.md#bulk-bytes):

* **a tool result** past ``LINK_OUTBOUND_INLINE_BYTES`` -- the images
  ``view_image`` or ``pod_view_document_pages`` return reach 5 MiB -- is
  staged, and the ``mcp_ok`` names it (``{"lemma_staged": {...}}``);
* **a command** whose payload is past it -- a START_RUN with a very long
  prompt -- has its payload staged the same way, and commands are packed into
  as many frames as they need;
* and whatever still does not fit is refused by ``LinkWriter.send_frame``, the
  one guard every outbound frame passes.

A host released before staged bodies cannot fetch one. For it a tool result
loses its images, last first, until it fits, and says so in a text block the
agent reads; a command too large for a frame is not sent to it at all.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from app.modules.agent.agent_host.domain.host import AgentHostCommand
from app.modules.agent.agent_host.domain.transfer import (
    LINK_OUTBOUND_FRAME_BYTES,
    LINK_OUTBOUND_INLINE_BYTES,
    STAGED_BODY_MAX_BYTES,
    STAGED_COMMAND_GRACE_SECONDS,
    STAGED_FIELD,
    STAGED_RESULT_TTL_SECONDS,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.modules.agent.agent_host.control.transfers import (
    AgentHostTransferStore,
)

logger = get_logger(__name__)

#: What an older host's agent is told in place of the images that were left
#: out of a tool result.
IMAGES_LEFT_OUT = (
    "{count} image(s) from this tool result were left out: together they are "
    "larger than this version of Lemma Desktop can receive. Updating Lemma "
    "Desktop delivers them."
)

_MEDIA_TYPES = frozenset({"image", "audio", "resource"})


def encoded(value: object) -> bytes:
    """``value`` as the writer puts it in a frame."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def encoded_size(value: object) -> int:
    """Bytes ``value`` takes in a frame."""
    return len(encoded(value))


class OutboundStager:
    """Stages what is too large for one host's link; trims for an old host."""

    def __init__(
        self,
        *,
        host_id: UUID,
        host_fetches_staged: bool,
        store: AgentHostTransferStore | None = None,
    ) -> None:
        self._host_id = host_id
        self._fetches = host_fetches_staged
        self._store = store or AgentHostTransferStore()

    async def tool_result(self, result: JsonObject) -> JsonObject:
        """The ``mcp_ok`` result as this host can be sent it."""
        # Off the loop: a result with images is megabytes of JSON.
        body = await run_blocking(encoded, result)
        if len(body) <= LINK_OUTBOUND_INLINE_BYTES:
            return result
        if not self._fetches:
            return await run_blocking(
                without_media_past, result, LINK_OUTBOUND_INLINE_BYTES
            )
        return await self._staged(result, body, ttl_seconds=STAGED_RESULT_TTL_SECONDS)

    async def commands(
        self, commands: list[AgentHostCommand]
    ) -> list[AgentHostCommand]:
        """Commands with each oversized payload staged.

        A command still too large -- for a host that cannot fetch -- is left
        out; its caller sends it nowhere, and logs that it could not.
        """
        fitted: list[AgentHostCommand] = []
        for command in commands:
            body = await run_blocking(encoded, command.payload)
            if len(body) <= LINK_OUTBOUND_INLINE_BYTES:
                fitted.append(command)
            elif self._fetches:
                ttl = int(
                    (command.expires_at - datetime.now(timezone.utc)).total_seconds()
                )
                fitted.append(
                    command.model_copy(
                        update={
                            "payload": await self._staged(
                                command.payload,
                                body,
                                ttl_seconds=max(1, ttl) + STAGED_COMMAND_GRACE_SECONDS,
                            )
                        }
                    )
                )
        return fitted

    async def _staged(
        self, value: JsonObject, body: bytes, *, ttl_seconds: int
    ) -> JsonObject:
        if len(body) > STAGED_BODY_MAX_BYTES:
            # Sent as it is, the guard refuses it with a clear error.
            return value
        staged = await self._store.stage_body_for_host(
            host_id=self._host_id, body=body, ttl_seconds=ttl_seconds
        )
        return {STAGED_FIELD: staged.wire()}


async def command_frames(
    stager: OutboundStager | None,
    commands: list[AgentHostCommand],
    *,
    host_id: UUID | None,
) -> list[list[AgentHostCommand]]:
    """Commands ready to send: oversized payloads staged, packed into frames.

    One a host cannot be sent at all is logged and left out; it stays in the
    queue, and the next push tries again.
    """
    fitted = await stager.commands(commands) if stager is not None else commands
    kept = {command.command_id for command in fitted}
    for command in commands:
        if command.command_id not in kept:
            logger.warning(
                "agent.agent_host_link.command_too_large",
                host_id=str(host_id) if host_id else None,
                command_id=str(command.command_id),
                command_kind=command.kind.value,
            )
    return pack_commands(fitted)


def pack_commands(
    commands: list[AgentHostCommand], budget: int = LINK_OUTBOUND_FRAME_BYTES
) -> list[list[AgentHostCommand]]:
    """Commands in order, in as few frames as each stays within ``budget``."""
    frames: list[list[AgentHostCommand]] = []
    size = 0
    for command in commands:
        cost = encoded_size(command.model_dump(mode="json"))
        if not frames or size + cost > budget:
            frames.append([])
            size = 0
        frames[-1].append(command)
        size += cost
    return frames


def without_media_past(result: JsonObject, budget: int) -> JsonObject:
    """``result`` with its images dropped, last first, until it fits.

    For a host that cannot fetch a staged result: the text and structured
    content are what the agent most needs, and a frame it is refused loses
    them too. A note says how many images were left out.
    """
    content = result.get("content")
    if not isinstance(content, list):
        return result
    kept = list(content)
    dropped = 0
    while encoded_size({**result, "content": kept}) > budget:
        index = next(
            (
                position
                for position in range(len(kept) - 1, -1, -1)
                if isinstance(kept[position], dict)
                and kept[position].get("type") in _MEDIA_TYPES
            ),
            None,
        )
        if index is None:
            break
        del kept[index]
        dropped += 1
    if dropped:
        kept.append({"type": "text", "text": IMAGES_LEFT_OUT.format(count=dropped)})
    return {**result, "content": kept}


__all__ = [
    "command_frames",
    "IMAGES_LEFT_OUT",
    "OutboundStager",
    "encoded_size",
    "pack_commands",
    "without_media_past",
]
