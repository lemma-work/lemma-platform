from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import AgentEventType, MessageKind
from app.modules.agent.infrastructure import daemon_hub


class _Messages:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    async def __aiter__(self) -> AsyncIterator[object]:
        for value in self._values:
            yield value


class _Channel:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    @asynccontextmanager
    async def subscribe(self, channels: list[str]):
        assert len(channels) == 1
        yield _Messages(self._values)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


class _WebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)


@pytest.mark.parametrize(
    ("provider_name", "canonical_name"),
    [
        ("mcp__lemma_tools__lemma_display_resource", "display_resource"),
        ("mcp.lemma_tools.lemma_ask_user", "ask_user"),
        ("lemma_tools_lemma_exec_command", "exec_command"),
        ("lemma_display_resource", "display_resource"),
        ("commandExecution", "commandExecution"),
    ],
)
def test_daemon_message_tool_names_are_canonicalized(
    provider_name: str, canonical_name: str
) -> None:
    event = daemon_hub._event_from_payload(
        {
            "type": "message",
            "data": {
                "role": "assistant",
                "kind": "tool_call",
                "tool_name": provider_name,
                "tool_call_id": "call-1",
                "tool_args": {"value": 1},
            },
        },
        agent_run_id=uuid4(),
    )

    assert event.data.kind == MessageKind.TOOL_CALL
    assert event.data.tool_name == canonical_name
    if provider_name == canonical_name:
        assert not (event.data.metadata or {}).get("provider_tool_name")
    else:
        assert event.data.metadata == {"provider_tool_name": provider_name}


def test_daemon_tool_token_name_is_canonicalized() -> None:
    event = daemon_hub._event_from_payload(
        {
            "type": "token",
            "data": {
                "kind": "tool",
                "data": '{"tool_name":"mcp__lemma_tools__lemma_exec_command","args":{"cmd":"pwd"}}',
            },
        },
        agent_run_id=uuid4(),
    )

    assert event.data == {
        "kind": "tool",
        "data": '{"tool_name":"exec_command","args":{"cmd":"pwd"}}',
    }


def test_daemon_tool_return_keeps_call_id_and_result_while_normalizing_name() -> None:
    event = daemon_hub._event_from_payload(
        {
            "type": "message",
            "data": {
                "role": "tool",
                "kind": "tool_return",
                "tool_name": "mcp__lemma_tools__lemma_display_resource",
                "tool_call_id": "display-1",
                "tool_result": {"success": True, "url": "/widgets/1"},
            },
        },
        agent_run_id=uuid4(),
    )

    assert event.data.kind == MessageKind.TOOL_RETURN
    assert event.data.tool_name == "display_resource"
    assert event.data.tool_call_id == "display-1"
    assert event.data.tool_result == {"success": True, "url": "/widgets/1"}
    assert event.data.metadata == {
        "provider_tool_name": "mcp__lemma_tools__lemma_display_resource"
    }


@pytest.mark.asyncio
async def test_daemon_command_listener_filters_and_forwards(monkeypatch):
    daemon_id = uuid4()
    user_id = uuid4()
    websocket = _WebSocket()
    redis = _Redis()
    channel = _Channel(
        [
            "not-json",
            {
                "type": "run.start",
                "daemon_id": str(daemon_id),
                "user_id": str(user_id),
                "agent_run_id": str(uuid4()),
            },
        ]
    )

    async def get_channel():
        return channel

    monkeypatch.setattr(daemon_hub, "get_channel_service", get_channel)
    monkeypatch.setattr(daemon_hub, "_get_redis", lambda: redis)
    connection = daemon_hub._DaemonConnection(
        daemon_id=daemon_id,
        user_id=user_id,
        websocket=websocket,  # type: ignore[arg-type]
    )

    await daemon_hub.AgentRuntimeDaemonHub()._listen_for_daemon_commands(connection)

    assert connection.command_ready.is_set()
    assert len(websocket.sent) == 1
    online_key = daemon_hub._daemon_online_key(daemon_id)
    assert redis.values[online_key] == str(user_id)
    assert redis.deleted == [online_key]


@pytest.mark.asyncio
async def test_remote_run_listener_converts_channel_messages(monkeypatch):
    agent_run_id = uuid4()
    channel = _Channel(
        [
            {
                "agent_run_id": str(agent_run_id),
                "event": {"type": "status", "data": {"phase": "running"}},
            }
        ]
    )

    async def get_channel():
        return channel

    monkeypatch.setattr(daemon_hub, "get_channel_service", get_channel)
    queue: asyncio.Queue = asyncio.Queue()
    ready = asyncio.Event()

    await daemon_hub.AgentRuntimeDaemonHub()._listen_for_run_events(
        agent_run_id=agent_run_id,
        queue=queue,
        ready=ready,
    )

    event = queue.get_nowait()
    assert ready.is_set()
    assert event.type == AgentEventType.STATUS
    assert event.data == {"phase": "running"}


@pytest.mark.asyncio
async def test_close_agent_runtime_resources_orders_cleanup(monkeypatch):
    calls: list[str] = []

    async def close_hub() -> None:
        calls.append("hub")

    async def close_redis() -> None:
        calls.append("redis")

    monkeypatch.setattr(daemon_hub.agent_runtime_daemon_hub, "close", close_hub)
    monkeypatch.setattr(daemon_hub, "close_agent_runtime_redis", close_redis)

    await daemon_hub.close_agent_runtime_resources()

    assert calls == ["hub", "redis"]
