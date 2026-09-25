"""An ``op``, end to end across the Redis hop, against a fake socket.

The caller (``AgentHostOpClient``) and the link session holding the socket
share nothing but a channel service, exactly as two replicas do. The host is
the test: it reads the ``op`` frame off the fake socket and answers it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.agent.domain.agent_host_ops import (
    HOST_OFFLINE,
    HOST_OFFLINE_MESSAGE,
    LINK_LOST,
    AgentHostOpError,
)
from app.modules.agent.infrastructure.agent_host.channels import (
    OP,
    host_poke_channel,
    parse_host_notice,
)
from app.modules.agent.services.agent_host_ops import AgentHostOpClient
from app.modules.agent.tests.unit.agent_host_link_fakes import (
    FakeChannels,
    Link,
    hello,
)


def _deadline(seconds: float = 5.0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


async def _op_frame(link: Link) -> dict:
    while True:
        frame = await link.socket.frame()
        if frame["type"] == "op":
            return frame


def _request(link: Link, client: AgentHostOpClient, workspace=None, **kwargs):
    return asyncio.ensure_future(
        client.request(
            host_id=link.store.host_id,
            workspace=workspace or uuid4(),
            method=kwargs.get("method", "process.list"),
            params=kwargs.get("params", {}),
            deadline_at=kwargs.get("deadline_at", _deadline()),
        )
    )


async def test_an_op_reaches_the_host_and_its_answer_comes_back():
    link = Link()
    await link.open()
    client = AgentHostOpClient(link.channels)
    workspace = uuid4()

    pending = _request(
        link,
        client,
        workspace,
        method="file.stat",
        params={"path": "/Users/me/p/a.txt"},
    )
    frame = await _op_frame(link)

    # A server id, in its own namespace, and the body the contract names.
    assert frame["id"].startswith("s")
    assert frame["body"]["workspace"] == str(workspace)
    assert frame["body"]["method"] == "file.stat"
    assert frame["body"]["params"] == {"path": "/Users/me/p/a.txt"}
    assert 0 < frame["body"]["deadline_ms"] <= 5000

    link.socket.send_raw(
        {
            "type": "websocket.receive",
            "text": (
                '{"type":"op_ok","re":"%s","body":{"result":{"kind":"file"}}}'
                % frame["id"]
            ),
        }
    )
    assert await pending == {"kind": "file"}


async def test_nobody_picking_the_op_up_is_the_mac_not_connected():
    channels = FakeChannels()
    client = AgentHostOpClient(channels, pickup_timeout_seconds=0.05)

    with pytest.raises(AgentHostOpError) as raised:
        await client.request(
            host_id=uuid4(),
            workspace=uuid4(),
            method="process.list",
            params={},
            deadline_at=_deadline(),
        )

    assert raised.value.kind == HOST_OFFLINE
    assert raised.value.message == HOST_OFFLINE_MESSAGE
    assert "This Mac is not connected" in HOST_OFFLINE_MESSAGE


async def test_a_host_refusal_carries_its_detail_kind():
    link = Link()
    await link.open()
    pending = _request(link, AgentHostOpClient(link.channels))
    frame = await _op_frame(link)

    link.socket.send_raw(
        {
            "type": "websocket.receive",
            "text": (
                '{"type":"error","re":"%s","body":{"code":"OP_FAILED",'
                '"message":"no such file","retryable":false,'
                '"detail":{"kind":"not_found"}}}' % frame["id"]
            ),
        }
    )

    with pytest.raises(AgentHostOpError) as raised:
        await pending
    assert raised.value.kind == "not_found"
    assert raised.value.message == "no such file"


async def test_an_error_about_the_hosts_own_request_is_not_an_op_answer():
    """Ids are matched against the relay's own ``s`` ids only."""
    link = Link()
    await link.open()
    pending = _request(link, AgentHostOpClient(link.channels))
    frame = await _op_frame(link)

    link.socket.send_raw(
        {
            "type": "websocket.receive",
            "text": '{"type":"error","re":"7","body":{"code":"X","message":"m"}}',
        }
    )
    await asyncio.sleep(0.05)
    assert not pending.done()

    link.socket.send_raw(
        {
            "type": "websocket.receive",
            "text": '{"type":"op_ok","re":"%s","body":{"result":{}}}' % frame["id"],
        }
    )
    assert await pending == {}


async def test_only_one_link_takes_an_op_when_two_hear_it():
    channels = FakeChannels()
    first = Link(channels=channels)
    await first.open()
    second = Link(channels=channels, store=first.store)
    # The second link supersedes the first on hello, so give it its own
    # subscription without the takeover: drive only its relay.
    workspace = uuid4()
    notice = {
        "type": OP,
        "op_id": "abc",
        "reply": "reply-channel",
        "workspace": str(workspace),
        "method": "process.list",
        "params": {},
        "deadline_ms": 1000,
    }
    first.session.ops.accept(notice, host_id=first.store.host_id)
    second.session.ops.accept(notice, host_id=first.store.host_id)
    frame = await _op_frame(first)
    await asyncio.sleep(0.05)

    assert frame["body"]["method"] == "process.list"
    assert second.socket.nothing_sent()
    assert sum(1 for c, _ in channels.published if c == "reply-channel") == 1


async def test_a_link_that_closes_mid_op_says_so():
    link = Link()
    await link.open()
    pending = _request(link, AgentHostOpClient(link.channels))
    await _op_frame(link)

    link.socket.hang_up()
    await link.ended()

    with pytest.raises(AgentHostOpError) as raised:
        await pending
    assert raised.value.kind == LINK_LOST


async def test_an_op_notice_does_not_reread_the_command_queue():
    link = Link()
    await link.open()
    await asyncio.sleep(0.05)
    reads = link.store.reads
    pending = _request(link, AgentHostOpClient(link.channels))
    frame = await _op_frame(link)
    link.socket.send_raw(
        {
            "type": "websocket.receive",
            "text": '{"type":"op_ok","re":"%s","body":{"result":{}}}' % frame["id"],
        }
    )
    await pending

    assert link.store.reads == reads


def test_an_op_notice_parses_as_an_op_and_keeps_its_payload():
    notice = parse_host_notice('{"type":"op","op_id":"x"}')

    assert notice.kind == OP
    assert notice.payload == {"type": "op", "op_id": "x"}
    assert host_poke_channel(uuid4()).endswith(":poke")


async def test_hello_capabilities_reach_the_store():
    link = Link()
    link.start()
    frame_id = link.socket.send(
        "hello",
        {
            "hello": hello(),
            "host_execution": {
                "enabled": True,
                "platform": "macos",
                "available": True,
            },
        },
    )
    await link.socket.answer_to(frame_id)

    capability = link.store.host_execution
    assert capability.enabled and capability.available and capability.usable
    assert capability.platform == "macos"
