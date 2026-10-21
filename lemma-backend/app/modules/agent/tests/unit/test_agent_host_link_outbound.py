"""Nothing Lemma sends a host passes the link's 1 MiB message ceiling.

A 5 MiB image tool result and a START_RUN with a 2 MiB prompt, through the
real session over a fake socket: the parts too large to send are staged for the
host to fetch, commands are packed into frames that fit, an older host is sent
a result without its images, and the one guard every outbound frame passes
refuses anything that still does not fit. See
docs/architecture/agent-host.md#bulk-bytes.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid7

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from app.core.server_limits import WS_MAX_BYTES
from app.modules.agent.domain.agent_host import AgentHostCommand, AgentHostCommandKind
from app.modules.agent.domain.agent_host_link import ServerFrameType
from app.modules.agent.domain.agent_host_transfer import STAGED_FIELD
from app.modules.agent.services.agent_host_link_outbound import (
    IMAGES_LEFT_OUT,
    encoded_size,
    pack_commands,
)
from app.modules.agent.services.agent_host_link_wire import (
    EmptyBody,
    LinkWriter,
    OutboundFrameTooLarge,
)
from app.modules.agent.tests.unit.agent_host_link_fakes import (
    TOKEN,
    FakeSocket,
    Link,
)

pytestmark = pytest.mark.asyncio

FIVE_MIB_PNG = b"\x89PNG\r\n\x1a\n" + b"\x07" * (5 * 1024 * 1024 - 8)


def _image_result() -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(type="text", text="the page"),
            ImageContent(
                type="image",
                data=base64.b64encode(FIVE_MIB_PNG).decode(),
                mimeType="image/png",
            ),
        ]
    )


def _call(link: Link) -> dict:
    return {
        "run_id": str(uuid7()),
        "conversation_id": str(link.conversation_id),
        "token": TOKEN,
        "method": "tools/call",
        "params": {"name": "view_image", "arguments": {}},
    }


async def test_a_five_mebibyte_image_result_is_staged_for_the_host_to_fetch():
    link = Link()
    link.mcp_service.call_result = _image_result()
    await link.open()
    try:
        answer = await link.socket.answer_to(link.socket.send("mcp", _call(link)))
        assert answer["type"] == "mcp_ok", answer
        staged = answer["body"]["result"][STAGED_FIELD]
        assert link.socket.largest_sent < 64 * 1024

        body, digest = await link.transfers.fetch_by_host(
            staged["id"], host_id=link.store.host_id
        )
        assert digest == staged["sha256"] and len(body) == staged["size"]
        result = json.loads(body)
        image = next(block for block in result["content"] if block["type"] == "image")
        assert base64.b64decode(image["data"]) == FIVE_MIB_PNG
        # Reusable: a retried call's copy of the answer resolves too.
        assert (
            await link.transfers.fetch_by_host(staged["id"], host_id=link.store.host_id)
        )[0] == body
    finally:
        await link.close()


async def test_an_older_host_is_sent_the_result_without_its_images():
    link = Link()
    link.mcp_service.call_result = _image_result()
    await link.open(staged_bodies=False)
    try:
        answer = await link.socket.answer_to(link.socket.send("mcp", _call(link)))
        assert answer["type"] == "mcp_ok", answer
        content = answer["body"]["result"]["content"]
        assert [block["type"] for block in content] == ["text", "text"]
        assert content[0]["text"] == "the page"
        assert content[1]["text"] == IMAGES_LEFT_OUT.format(count=1)
        assert link.socket.largest_sent < WS_MAX_BYTES
    finally:
        await link.close()


def _start_run(prompt_bytes: int) -> AgentHostCommand:
    now = datetime.now(timezone.utc)
    return AgentHostCommand(
        command_id=uuid7(),
        kind=AgentHostCommandKind.START_RUN,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        run_id=uuid7(),
        lease_epoch=1,
        payload={"prompt": [{"type": "text", "text": "p" * prompt_bytes}]},
    )


async def test_an_oversized_command_is_staged_and_the_rest_are_packed_to_fit():
    link = Link()
    huge = _start_run(2 * 1024 * 1024)
    others = [_start_run(300 * 1024) for _ in range(6)]
    link.store.queue = [huge, *others]
    await link.open()
    try:
        control = link.socket.send("control", {})
        delivered: dict[str, dict] = {}
        answer = await link.socket.answer_to(control)
        assert answer["type"] == "control_ok"
        for command in answer["body"]["commands"]:
            delivered[command["command_id"]] = command
        while len(delivered) < 7:
            frame = await link.socket.frame()
            assert frame["type"] == ServerFrameType.COMMANDS.value, frame
            for command in frame["body"]["commands"]:
                assert command["command_id"] not in delivered
                delivered[command["command_id"]] = command
        assert link.socket.largest_sent <= WS_MAX_BYTES

        staged = delivered[str(huge.command_id)]["payload"][STAGED_FIELD]
        body, _ = await link.transfers.fetch_by_host(
            staged["id"], host_id=link.store.host_id
        )
        assert json.loads(body) == huge.payload
        for command in others:
            assert delivered[str(command.command_id)]["payload"] == command.payload
    finally:
        await link.close()


async def test_an_older_host_is_not_sent_a_command_too_large_for_a_frame():
    link = Link()
    huge = _start_run(2 * 1024 * 1024)
    small = _start_run(10)
    link.store.queue = [huge, small]
    await link.open(staged_bodies=False)
    try:
        answer = await link.socket.answer_to(link.socket.send("control", {}))
        sent = [command["command_id"] for command in answer["body"]["commands"]]
        assert sent == [str(small.command_id)]
        assert link.socket.largest_sent < WS_MAX_BYTES
    finally:
        await link.close()


def test_commands_are_packed_in_order_into_frames_that_fit():
    commands = [_start_run(200 * 1024) for _ in range(12)]
    frames = pack_commands(commands, budget=700 * 1024)
    assert [command for frame in frames for command in frame] == commands
    for frame in frames:
        assert (
            encoded_size([command.model_dump(mode="json") for command in frame])
            <= 700 * 1024
        )


async def test_the_guard_answers_a_request_whose_answer_is_too_large():
    socket = FakeSocket()
    writer = LinkWriter(socket, on_lost=lambda: None)

    class Big(EmptyBody):
        text: str

    await writer.send_frame(
        ServerFrameType.MCP_OK, Big(text="x" * WS_MAX_BYTES), re="7"
    )
    refused = await socket.frame()
    assert refused["type"] == "error" and refused["re"] == "7"
    assert refused["body"]["code"] == "FRAME_TOO_LARGE"
    assert refused["body"]["retryable"] is False
    assert socket.largest_sent < 4096

    # A push, or a request of Lemma's own, is the sender's to fail.
    with pytest.raises(OutboundFrameTooLarge):
        await writer.send_frame(ServerFrameType.COMMANDS, Big(text="x" * WS_MAX_BYTES))
    with pytest.raises(OutboundFrameTooLarge):
        await writer.send_frame(
            ServerFrameType.OP, Big(text="x" * WS_MAX_BYTES), id="s1"
        )
    assert socket.nothing_sent()
