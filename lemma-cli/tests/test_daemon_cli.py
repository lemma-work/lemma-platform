from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from lemma_cli.cli_core.commands import daemon


def test_daemon_turn_timeout_defaults_to_two_hours(monkeypatch):
    monkeypatch.delenv(daemon.DAEMON_TURN_TIMEOUT_SECONDS_ENV, raising=False)

    assert daemon._daemon_turn_timeout_seconds() == 7200.0


def test_daemon_turn_timeout_uses_env_with_minimum(monkeypatch):
    monkeypatch.setenv(daemon.DAEMON_TURN_TIMEOUT_SECONDS_ENV, "0.2")

    assert daemon._daemon_turn_timeout_seconds() == 1.0


def test_daemon_turn_timeout_ignores_invalid_env(monkeypatch):
    monkeypatch.setenv(daemon.DAEMON_TURN_TIMEOUT_SECONDS_ENV, "nope")

    assert daemon._daemon_turn_timeout_seconds() == 7200.0


def test_codex_worker_ttl_defaults_to_two_hours(monkeypatch):
    monkeypatch.delenv(daemon.CODEX_WORKER_TTL_SECONDS_ENV, raising=False)

    assert daemon._codex_worker_ttl_seconds() == 7200.0


def test_codex_completed_assistant_message_extracts_final_text():
    assert (
        daemon._codex_completed_assistant_text(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "id": "msg-1",
                        "type": "agentMessage",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Done from item."}],
                    }
                },
            }
        )
        == "Done from item."
    )


def test_codex_completed_tool_item_is_not_assistant_text():
    assert (
        daemon._codex_completed_assistant_text(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "id": "call-1",
                        "type": "mcpToolCall",
                        "tool": "lemma_exec_command",
                        "arguments": {"cmd": "pwd"},
                        "result": {"structuredContent": {"stdout": "/workspace"}},
                    }
                },
            }
        )
        is None
    )


def test_codex_completed_assistant_message_only_adds_missing_suffix():
    assert (
        daemon._codex_new_completed_assistant_text(
            ["Checking ", "now."],
            ["Checking ", "now."],
            "Checking now.",
        )
        is None
    )
    assert (
        daemon._codex_new_completed_assistant_text(
            ["Checking "],
            ["Checking "],
            "Checking now.",
        )
        == "now."
    )


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


@pytest.mark.asyncio
async def test_stop_active_run_acks_orphaned_run():
    websocket = _FakeWebSocket()

    await daemon._stop_active_run(
        websocket=websocket,
        active_runs={},
        agent_run_id="run-orphaned",
    )

    assert websocket.messages == [
        {
            "type": "run.event",
            "agent_run_id": "run-orphaned",
            "event": {"type": "stopped", "data": {}},
        }
    ]


@pytest.mark.asyncio
async def test_stop_active_run_cancels_local_task_without_duplicate_ack():
    websocket = _FakeWebSocket()
    task = asyncio.create_task(asyncio.sleep(60))
    try:
        await daemon._stop_active_run(
            websocket=websocket,
            active_runs={"run-active": task},
            agent_run_id="run-active",
        )

        assert task.cancelled() or task.cancelling()
        assert websocket.messages == []
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_daemon_run_start_executes_configured_provider_command(monkeypatch):
    websocket = _FakeWebSocket()
    monkeypatch.setenv(
        "LEMMA_DAEMON_CLAUDE_CODE_COMMAND",
        f"{sys.executable} -c \"print('assistant ok')\"",
    )

    await daemon.handle_run_start(
        websocket,
        {
            "type": "run.start",
            "agent_run_id": "run-1",
            "payload": {
                "harness_kind": "CLAUDE_CODE",
                "model_name": "gpt-5.5",
                "prompt": {
                    "system_prompt": "system",
                    "user_prompt": "hello daemon",
                },
                "mcp": {
                    "url": "http://localhost/mcp",
                    "server_name": "lemma_tools",
                    "conversation_id": "conversation-1",
                    "authorization": "Bearer test-token",
                    "token": "test-token",
                },
            },
        },
    )

    event_types = [message["event"]["type"] for message in websocket.messages]
    assert event_types == ["status", "token", "message", "completed"]
    assert websocket.messages[1]["event"]["data"] == "assistant ok"
    assert websocket.messages[2]["event"]["data"]["kind"] == "text"
    assert websocket.messages[2]["event"]["data"]["text"] == "assistant ok"


@pytest.mark.asyncio
async def test_daemon_does_not_emit_prompt_echo_from_provider_stdout(monkeypatch):
    websocket = _FakeWebSocket()
    monkeypatch.setenv(
        "LEMMA_DAEMON_CLAUDE_CODE_COMMAND",
        f"{sys.executable} -c \"import sys; print(sys.stdin.read())\"",
    )

    await daemon.handle_run_start(
        websocket,
        {
            "type": "run.start",
            "agent_run_id": "run-echo",
            "payload": {
                "harness_kind": "CLAUDE_CODE",
                "model_name": "gpt-5.5",
                "prompt": {
                    "system_prompt": "system",
                    "user_prompt": "hello daemon",
                },
                "mcp": {
                    "url": "http://localhost/mcp",
                    "server_name": "lemma_tools",
                    "conversation_id": "conversation-echo",
                    "authorization": "Bearer test-token",
                    "token": "test-token",
                },
            },
        },
    )

    assert [message["event"]["type"] for message in websocket.messages] == [
        "status",
        "completed",
    ]


@pytest.mark.asyncio
async def test_daemon_strips_prompt_echo_prefix_from_provider_stdout(monkeypatch):
    websocket = _FakeWebSocket()
    monkeypatch.setenv(
        "LEMMA_DAEMON_CLAUDE_CODE_COMMAND",
        (
            f"{sys.executable} -c \"import sys; "
            "print(sys.stdin.read() + '\\nassistant answer')\""
        ),
    )

    await daemon.handle_run_start(
        websocket,
        {
            "type": "run.start",
            "agent_run_id": "run-echo-prefix",
            "payload": {
                "harness_kind": "CLAUDE_CODE",
                "model_name": "gpt-5.5",
                "prompt": {
                    "system_prompt": "system",
                    "user_prompt": "hello daemon",
                },
                "mcp": {
                    "url": "http://localhost/mcp",
                    "server_name": "lemma_tools",
                    "conversation_id": "conversation-echo-prefix",
                    "authorization": "Bearer test-token",
                    "token": "test-token",
                },
            },
        },
    )

    event_types = [message["event"]["type"] for message in websocket.messages]
    assert event_types == ["status", "token", "message", "completed"]
    assert websocket.messages[1]["event"]["data"] == "assistant answer"
    assert websocket.messages[2]["event"]["data"]["kind"] == "text"
    assert websocket.messages[2]["event"]["data"]["text"] == "assistant answer"


@pytest.mark.asyncio
async def test_codex_app_server_tool_events_stream_as_agent_tokens_and_messages(
    monkeypatch,
    tmp_path,
):
    websocket = _FakeWebSocket()
    monkeypatch.setattr(daemon, "provider_cwd", lambda _harness_kind: tmp_path)
    monkeypatch.setattr(daemon, "_CODEX_APP_SERVER_POOL", daemon._CodexAppServerPool())
    monkeypatch.setattr(daemon, "_JsonRpcProcess", _FakeCodexJsonRpcProcess)

    try:
        await daemon.handle_run_start(
            websocket,
            {
                "type": "run.start",
                "agent_run_id": "run-codex",
                "payload": {
                    "harness_kind": "CODEX",
                    "model_name": "default",
                    "prompt": {
                        "system_prompt": "system",
                        "user_prompt": "use the tool",
                    },
                    "mcp": {
                        "url": "http://localhost/mcp",
                        "server_name": "lemma_tools",
                        "conversation_id": "conversation-codex",
                        "authorization": "Bearer test-token",
                        "token": "test-token",
                    },
                },
            },
        )
    finally:
        await daemon._CODEX_APP_SERVER_POOL.close()

    events = [message["event"] for message in websocket.messages]
    event_types = [event["type"] for event in events]
    assert event_types == [
        "status",
        "status",
        "token",
        "message",
        "token",
        "message",
        "token",
        "message",
        "token",
        "message",
        "completed",
    ]

    assert events[1]["data"]["status"] == "daemon.session.started"
    assert events[1]["data"]["local_session"] == {
        "harness_kind": "CODEX",
        "session_id": "thread-1",
    }
    assert events[2]["data"] == {"kind": "text", "data": "Intro "}
    assert events[3]["data"]["kind"] == "text"
    assert events[3]["data"]["text"] == "Intro"
    assert events[3]["data"]["metadata"]["is_final_answer"] is False
    tool_token = events[4]["data"]
    assert tool_token["kind"] == "tool"
    assert json.loads(tool_token["data"]) == {
        "tool_name": "lemma_exec_command",
        "args": {"cmd": "printf OK"},
    }
    assert events[5]["data"]["role"] == "assistant"
    assert events[5]["data"]["kind"] == "tool_call"
    assert events[5]["data"]["tool_name"] == "lemma_exec_command"
    assert events[5]["data"]["tool_call_id"] == "call-1"
    assert events[5]["data"]["tool_args"] == {"cmd": "printf OK"}
    assert events[6]["data"] == {"kind": "text", "data": "Before "}
    assert events[7]["data"]["role"] == "tool"
    assert events[7]["data"]["kind"] == "tool_return"
    assert events[7]["data"]["tool_name"] == "lemma_exec_command"
    assert events[7]["data"]["tool_call_id"] == "call-1"
    assert events[7]["data"]["tool_result"] == {"stdout": "OK"}
    assert events[8]["data"] == {"kind": "text", "data": "After"}
    assert events[9]["data"]["kind"] == "text"
    assert events[9]["data"]["text"] == "Before After"
    text_token_payloads = [
        event["data"]["data"]
        for event in events
        if event["type"] == "token"
        and isinstance(event["data"], dict)
        and event["data"].get("kind") == "text"
    ]
    assert not any("context" in payload for payload in text_token_payloads)
    text_messages = [
        event["data"]["text"]
        for event in events
        if event["type"] == "message"
        and event["data"].get("kind") == "text"
    ]
    assert not any("context" in message for message in text_messages)


@pytest.mark.asyncio
async def test_codex_app_server_pool_allows_parallel_runs(monkeypatch, tmp_path):
    websocket_one = _FakeWebSocket()
    websocket_two = _FakeWebSocket()
    monkeypatch.setattr(daemon, "provider_cwd", lambda _harness_kind: tmp_path)
    monkeypatch.setattr(daemon, "_CODEX_APP_SERVER_POOL", daemon._CodexAppServerPool())
    _SlowFakeCodexJsonRpcProcess.active_turns = 0
    _SlowFakeCodexJsonRpcProcess.max_active_turns = 0
    _SlowFakeCodexJsonRpcProcess.instances = []
    monkeypatch.setattr(daemon, "_JsonRpcProcess", _SlowFakeCodexJsonRpcProcess)

    async def run(websocket: _FakeWebSocket, run_id: str) -> None:
        await daemon.handle_run_start(
            websocket,
            {
                "type": "run.start",
                "agent_run_id": run_id,
                "payload": {
                    "harness_kind": "CODEX",
                    "model_name": "default",
                    "prompt": {
                        "system_prompt": "system",
                        "user_prompt": "say hi",
                    },
                    "mcp": {
                        "url": f"http://localhost/{run_id}/mcp",
                        "server_name": "lemma_tools",
                        "conversation_id": run_id,
                        "authorization": f"Bearer {run_id}-token",
                        "token": f"{run_id}-token",
                    },
                },
            },
        )

    try:
        await asyncio.gather(run(websocket_one, "run-one"), run(websocket_two, "run-two"))
    finally:
        await daemon._CODEX_APP_SERVER_POOL.close()

    assert len(_SlowFakeCodexJsonRpcProcess.instances) == 2
    assert _SlowFakeCodexJsonRpcProcess.max_active_turns == 2
    assert [message["event"]["type"] for message in websocket_one.messages] == [
        "status",
        "status",
        "token",
        "message",
        "completed",
    ]
    assert [message["event"]["type"] for message in websocket_two.messages] == [
        "status",
        "status",
        "token",
        "message",
        "completed",
    ]


@pytest.mark.asyncio
async def test_codex_app_server_pool_reuses_worker_with_saved_thread(
    monkeypatch,
    tmp_path,
):
    websocket_one = _FakeWebSocket()
    websocket_two = _FakeWebSocket()
    monkeypatch.setattr(daemon, "provider_cwd", lambda _harness_kind: tmp_path)
    monkeypatch.setattr(daemon, "_CODEX_APP_SERVER_POOL", daemon._CodexAppServerPool())
    _FakeCodexJsonRpcProcess.instances = []
    _FakeCodexJsonRpcProcess.next_thread_id = 0
    monkeypatch.setattr(daemon, "_JsonRpcProcess", _FakeCodexJsonRpcProcess)

    payload = {
        "harness_kind": "CODEX",
        "model_name": "default",
        "prompt": {
            "system_prompt": "# Instructions\nRemember user facts.",
            "user_prompt": "USER:\nmy code word is alpha",
        },
        "mcp": {
            "url": "http://localhost/conversation-reuse/mcp",
            "server_name": "lemma_tools",
            "conversation_id": "conversation-reuse",
            "authorization": "Bearer reuse-token",
            "token": "reuse-token",
        },
    }
    try:
        await daemon.handle_run_start(
            websocket_one,
            {"type": "run.start", "agent_run_id": "run-one", "payload": payload},
        )
        payload = {
            **payload,
            "prompt": {
                "session_id": "thread-1",
                "user_prompt": "USER:\nwhat is my code word?",
            },
        }
        await daemon.handle_run_start(
            websocket_two,
            {"type": "run.start", "agent_run_id": "run-two", "payload": payload},
        )
    finally:
        await daemon._CODEX_APP_SERVER_POOL.close()

    assert len(_FakeCodexJsonRpcProcess.instances) == 1
    instance = _FakeCodexJsonRpcProcess.instances[0]
    thread_starts = [
        params
        for method, params in instance.requests
        if method == "thread/start"
    ]
    turn_starts = [
        params
        for method, params in instance.requests
        if method == "turn/start"
    ]
    assert thread_starts == [
        {"cwd": str(tmp_path / "conversations" / "conversation-reuse")}
    ]
    assert [params["threadId"] for params in turn_starts] == ["thread-1", "thread-1"]
    assert [
        params["input"][0]["text"]
        for params in turn_starts
    ] == [
        "# Instructions\nRemember user facts.\n\n# Conversation\nUSER:\nmy code word is alpha",
        "USER:\nwhat is my code word?",
    ]
    assert instance.closed is True
    assert [message["event"]["type"] for message in websocket_one.messages][-1] == "completed"
    assert [message["event"]["type"] for message in websocket_two.messages][-1] == "completed"


@pytest.mark.asyncio
async def test_codex_app_server_worker_closes_after_cancelled_turn(
    monkeypatch,
    tmp_path,
):
    websocket_one = _FakeWebSocket()
    websocket_two = _FakeWebSocket()
    monkeypatch.setattr(daemon, "provider_cwd", lambda _harness_kind: tmp_path)
    monkeypatch.setattr(daemon, "_CODEX_APP_SERVER_POOL", daemon._CodexAppServerPool())
    _HangingFakeCodexJsonRpcProcess.instances = []
    _FakeCodexJsonRpcProcess.instances = []
    monkeypatch.setattr(daemon, "_JsonRpcProcess", _HangingFakeCodexJsonRpcProcess)
    payload = {
        "harness_kind": "CODEX",
        "model_name": "default",
        "prompt": {
            "session_id": "thread-1",
            "user_prompt": "USER:\ncontinue",
        },
        "mcp": {
            "url": "http://localhost/conversation-cancel/mcp",
            "server_name": "lemma_tools",
            "conversation_id": "conversation-cancel",
            "authorization": "Bearer cancel-token",
            "token": "cancel-token",
        },
    }

    task = asyncio.create_task(
        daemon.handle_run_start(
            websocket_one,
            {"type": "run.start", "agent_run_id": "run-cancel", "payload": payload},
        )
    )
    try:
        for _ in range(20):
            if (
                _HangingFakeCodexJsonRpcProcess.instances
                and _HangingFakeCodexJsonRpcProcess.instances[0].requests
            ):
                break
            await asyncio.sleep(0.01)
        assert _HangingFakeCodexJsonRpcProcess.instances
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert _HangingFakeCodexJsonRpcProcess.instances[0].closed is True

        monkeypatch.setattr(daemon, "_JsonRpcProcess", _FakeCodexJsonRpcProcess)
        await daemon.handle_run_start(
            websocket_two,
            {"type": "run.start", "agent_run_id": "run-resume", "payload": payload},
        )
    finally:
        await daemon._CODEX_APP_SERVER_POOL.close()

    assert len(_FakeCodexJsonRpcProcess.instances) == 1
    turn_starts = [
        params
        for method, params in _FakeCodexJsonRpcProcess.instances[0].requests
        if method == "turn/start"
    ]
    assert [params["threadId"] for params in turn_starts] == ["thread-1"]
    assert [message["event"]["type"] for message in websocket_one.messages] == [
        "status",
        "stopped",
    ]
    assert [message["event"]["type"] for message in websocket_two.messages][-1] == "completed"


@pytest.mark.asyncio
async def test_codex_app_server_recovers_from_stale_saved_session(
    monkeypatch,
    tmp_path,
):
    websocket = _FakeWebSocket()
    monkeypatch.setattr(daemon, "provider_cwd", lambda _harness_kind: tmp_path)
    monkeypatch.setattr(daemon, "_CODEX_APP_SERVER_POOL", daemon._CodexAppServerPool())
    _FailingTurnFakeCodexJsonRpcProcess.instances = []
    _FailingTurnFakeCodexJsonRpcProcess.next_thread_id = 0
    monkeypatch.setattr(daemon, "_JsonRpcProcess", _FailingTurnFakeCodexJsonRpcProcess)

    try:
        await daemon.handle_run_start(
            websocket,
            {
                "type": "run.start",
                "agent_run_id": "run-stale",
                "payload": {
                    "harness_kind": "CODEX",
                    "model_name": "default",
                    "prompt": {
                        "session_id": "thread-expired",
                        "recovery_system_prompt": "# Instructions\nRecover cleanly.",
                        "user_prompt": "USER:\ncontinue",
                    },
                    "mcp": {
                        "url": "http://localhost/conversation-stale/mcp",
                        "server_name": "lemma_tools",
                        "conversation_id": "conversation-stale",
                        "authorization": "Bearer stale-token",
                        "token": "stale-token",
                    },
                },
            },
        )
    finally:
        await daemon._CODEX_APP_SERVER_POOL.close()

    events = [message["event"] for message in websocket.messages]
    assert [event["type"] for event in events[:3]] == ["status", "status", "status"]
    assert events[-1]["type"] == "completed"
    assert events[1]["data"] == {
        "status": "daemon.session.invalid",
        "local_session": {
            "harness_kind": "CODEX",
            "session_id": "thread-expired",
        },
    }
    assert events[2]["data"] == {
        "status": "daemon.session.started",
        "local_session": {
            "harness_kind": "CODEX",
            "session_id": "thread-1",
        },
    }
    instance = _FailingTurnFakeCodexJsonRpcProcess.instances[0]
    assert [
        (method, params["threadId"])
        for method, params in instance.requests
        if method == "turn/start"
    ] == [
        ("turn/start", "thread-expired"),
        ("turn/start", "thread-1"),
    ]
    retry_prompt = [
        params["input"][0]["text"]
        for method, params in instance.requests
        if method == "turn/start"
    ][1]
    assert "# Instructions\nRecover cleanly." in retry_prompt
    assert "USER:\ncontinue" in retry_prompt


@pytest.mark.asyncio
async def test_codex_app_server_flushes_completed_agent_message_items(
    monkeypatch,
    tmp_path,
):
    websocket = _FakeWebSocket()
    monkeypatch.setattr(daemon, "provider_cwd", lambda _harness_kind: tmp_path)
    monkeypatch.setattr(daemon, "_CODEX_APP_SERVER_POOL", daemon._CodexAppServerPool())
    _MultiMessageFakeCodexJsonRpcProcess.instances = []
    _MultiMessageFakeCodexJsonRpcProcess.next_thread_id = 0
    monkeypatch.setattr(daemon, "_JsonRpcProcess", _MultiMessageFakeCodexJsonRpcProcess)

    try:
        await daemon.handle_run_start(
            websocket,
            {
                "type": "run.start",
                "agent_run_id": "run-multi-message",
                "payload": {
                    "harness_kind": "CODEX",
                    "model_name": "default",
                    "prompt": {
                        "system_prompt": "system",
                        "user_prompt": "USER:\ncontinue",
                    },
                    "mcp": {
                        "url": "http://localhost/conversation-multi-message/mcp",
                        "server_name": "lemma_tools",
                        "conversation_id": "conversation-multi-message",
                        "authorization": "Bearer multi-token",
                        "token": "multi-token",
                    },
                },
            },
        )
    finally:
        await daemon._CODEX_APP_SERVER_POOL.close()

    events = [message["event"] for message in websocket.messages]
    text_messages = [
        event["data"]["text"]
        for event in events
        if event["type"] == "message"
        and event["data"]["role"] == "assistant"
        and event["data"].get("kind") == "text"
    ]
    assert text_messages == [
        "First durable assistant message.",
        "Second durable assistant message.",
    ]
    assert events[-1]["type"] == "completed"


@pytest.mark.asyncio
async def test_codex_app_server_strips_submitted_prompt_echo_from_stream(
    monkeypatch,
    tmp_path,
):
    websocket = _FakeWebSocket()
    monkeypatch.setattr(daemon, "provider_cwd", lambda _harness_kind: tmp_path)
    monkeypatch.setattr(daemon, "_CODEX_APP_SERVER_POOL", daemon._CodexAppServerPool())
    _PromptEchoFakeCodexJsonRpcProcess.instances = []
    monkeypatch.setattr(daemon, "_JsonRpcProcess", _PromptEchoFakeCodexJsonRpcProcess)

    try:
        await daemon.handle_run_start(
            websocket,
            {
                "type": "run.start",
                "agent_run_id": "run-prompt-echo",
                "payload": {
                    "harness_kind": "CODEX",
                    "model_name": "default",
                    "prompt": {
                        "system_prompt": "# Instructions\nHidden daemon system prompt",
                        "user_prompt": "USER:\nAlready saved user message",
                    },
                    "mcp": {
                        "url": "http://localhost/conversation-prompt-echo/mcp",
                        "server_name": "lemma_tools",
                        "conversation_id": "conversation-prompt-echo",
                        "authorization": "Bearer echo-token",
                        "token": "echo-token",
                    },
                },
            },
        )
    finally:
        await daemon._CODEX_APP_SERVER_POOL.close()

    events = [message["event"] for message in websocket.messages]
    assert [event["type"] for event in events] == [
        "status",
        "status",
        "token",
        "message",
        "completed",
    ]
    emitted_text = "\n".join(
        str(event["data"])
        for event in events
        if event["type"] in {"token", "message"}
    )
    assert "Hidden daemon system prompt" not in emitted_text
    assert "Already saved user message" not in emitted_text
    assert events[2]["data"] == {"kind": "text", "data": "assistant clean"}
    assert events[3]["data"]["kind"] == "text"
    assert events[3]["data"]["text"] == "assistant clean"


@pytest.mark.asyncio
async def test_codex_app_server_pool_uses_separate_threads_for_separate_conversations(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(daemon, "provider_cwd", lambda _harness_kind: tmp_path)
    monkeypatch.setattr(daemon, "_CODEX_APP_SERVER_POOL", daemon._CodexAppServerPool())
    _FakeCodexJsonRpcProcess.instances = []
    _FakeCodexJsonRpcProcess.next_thread_id = 0
    monkeypatch.setattr(daemon, "_JsonRpcProcess", _FakeCodexJsonRpcProcess)

    async def run(conversation_id: str, text: str) -> None:
        await daemon.handle_run_start(
            _FakeWebSocket(),
            {
                "type": "run.start",
                "agent_run_id": f"run-{conversation_id}",
                "payload": {
                    "harness_kind": "CODEX",
                    "model_name": "default",
                    "prompt": {
                        "system_prompt": "system",
                        "user_prompt": text,
                    },
                    "mcp": {
                        "url": f"http://localhost/{conversation_id}/mcp",
                        "server_name": "lemma_tools",
                        "conversation_id": conversation_id,
                        "authorization": f"Bearer {conversation_id}-token",
                        "token": f"{conversation_id}-token",
                    },
                },
            },
        )

    try:
        await run("conversation-one", "remember alpha")
        await run("conversation-two", "remember beta")
    finally:
        await daemon._CODEX_APP_SERVER_POOL.close()

    assert len(_FakeCodexJsonRpcProcess.instances) == 2
    turn_thread_ids = [
        params["threadId"]
        for instance in _FakeCodexJsonRpcProcess.instances
        for method, params in instance.requests
        if method == "turn/start"
    ]
    assert turn_thread_ids == ["thread-1", "thread-2"]


@pytest.mark.asyncio
async def test_codex_app_server_pool_closes_idle_worker_after_ttl(monkeypatch, tmp_path):
    websocket = _FakeWebSocket()
    monkeypatch.setenv("LEMMA_DAEMON_CODEX_WORKER_TTL_SECONDS", "0.01")
    monkeypatch.setattr(daemon, "provider_cwd", lambda _harness_kind: tmp_path)
    monkeypatch.setattr(daemon, "_CODEX_APP_SERVER_POOL", daemon._CodexAppServerPool())
    _FakeCodexJsonRpcProcess.instances = []
    monkeypatch.setattr(daemon, "_JsonRpcProcess", _FakeCodexJsonRpcProcess)

    try:
        await daemon.handle_run_start(
            websocket,
            {
                "type": "run.start",
                "agent_run_id": "run-ttl",
                "payload": {
                    "harness_kind": "CODEX",
                    "model_name": "default",
                    "prompt": {
                        "system_prompt": "system",
                        "user_prompt": "say hi",
                    },
                    "mcp": {
                        "url": "http://localhost/conversation-ttl/mcp",
                        "server_name": "lemma_tools",
                        "conversation_id": "conversation-ttl",
                        "authorization": "Bearer ttl-token",
                        "token": "ttl-token",
                    },
                },
            },
        )
        await asyncio.sleep(0.05)
        assert daemon._CODEX_APP_SERVER_POOL._workers == {}
        assert _FakeCodexJsonRpcProcess.instances[0].closed is True
    finally:
        await daemon._CODEX_APP_SERVER_POOL.close()


def test_codex_command_execution_output_delta_is_not_assistant_text():
    assert daemon._codex_text_delta(
        {
            "method": "item/commandExecution/outputDelta",
            "params": {
                "itemId": "cmd-1",
                "delta": '{\n  "context": "default"\n}\n',
            },
        }
    ) is None


def test_codex_command_execution_item_maps_to_tool_messages():
    started = daemon._codex_tool_call_event(
        {
            "method": "item/started",
            "params": {
                "item": {
                    "id": "cmd-1",
                    "type": "commandExecution",
                    "command": "lemma --output json context",
                    "cwd": "/Users/kapeed/lemma-codex",
                    "status": "running",
                }
            },
        }
    )
    completed = daemon._codex_tool_return_event(
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "id": "cmd-1",
                    "type": "commandExecution",
                    "command": "lemma --output json context",
                    "cwd": "/Users/kapeed/lemma-codex",
                    "status": "completed",
                    "exitCode": 0,
                    "aggregatedOutput": '{\n  "context": "default"\n}\n',
                }
            },
        }
    )

    assert started is not None
    assert started["kind"] == "tool_call"
    assert started["tool_name"] == "commandExecution"
    assert started["tool_call_id"] == "cmd-1"
    assert started["tool_args"] == {
        "command": "lemma --output json context",
        "cwd": "/Users/kapeed/lemma-codex",
    }
    assert completed is not None
    assert completed["kind"] == "tool_return"
    assert completed["tool_name"] == "commandExecution"
    assert completed["tool_call_id"] == "cmd-1"
    assert completed["tool_result"] == {
        "status": "completed",
        "exit_code": 0,
        "output": '{\n  "context": "default"\n}\n',
    }


@pytest.mark.asyncio
async def test_claude_stream_persists_assistant_text_before_tool_call(monkeypatch, tmp_path):
    websocket = _FakeWebSocket()
    script_path = tmp_path / "fake_claude_stream.py"
    script_path.write_text(
        textwrap.dedent(
            """
            import json
            import sys

            sys.stdin.read()
            print(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Checking now. "},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "lemma_exec_command",
                    "input": {"cmd": "pwd"},
                },
            ]}}))
            print(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Done."}
            ]}}))
            print(json.dumps({"type": "result", "result": "Done."}))
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "LEMMA_DAEMON_CLAUDE_CODE_COMMAND",
        f"{sys.executable} {script_path}",
    )

    await daemon.handle_run_start(
        websocket,
        {
            "type": "run.start",
            "agent_run_id": "run-claude",
            "payload": {
                "harness_kind": "CLAUDE_CODE",
                "model_name": "sonnet",
                "prompt": {
                    "system_prompt": "system",
                    "user_prompt": "use the tool",
                },
                "mcp": {
                    "url": "http://localhost/mcp",
                    "server_name": "lemma_tools",
                    "conversation_id": "conversation-claude",
                    "authorization": "Bearer claude-token",
                    "token": "claude-token",
                },
            },
        },
    )

    events = [message["event"] for message in websocket.messages]
    messages = [event["data"] for event in events if event["type"] == "message"]
    assert messages[0]["kind"] == "text"
    assert messages[0]["text"] == "Checking now."
    assert messages[0]["metadata"]["is_final_answer"] is False
    assert messages[1]["kind"] == "tool_call"
    assert messages[-1]["kind"] == "text"
    assert messages[-1]["text"] == "Done."
    assert [event["type"] for event in events][-1] == "completed"


@pytest.mark.asyncio
async def test_daemon_persists_claude_stream_session_id(monkeypatch, tmp_path):
    websocket = _FakeWebSocket()
    script_path = tmp_path / "claude_session.py"
    script_path.write_text(
        textwrap.dedent(
            """
            import json
            print(json.dumps({"type": "system", "subtype": "init", "session_id": "claude-session-1"}))
            print(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Hello from Claude."}
            ]}}))
            print(json.dumps({"type": "result", "result": "Hello from Claude."}))
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "LEMMA_DAEMON_CLAUDE_CODE_COMMAND",
        f"{sys.executable} {script_path}",
    )

    await daemon.handle_run_start(
        websocket,
        {
            "type": "run.start",
            "agent_run_id": "run-claude-session",
            "payload": {
                "harness_kind": "CLAUDE_CODE",
                "model_name": "sonnet",
                "prompt": {
                    "system_prompt": "system",
                    "user_prompt": "remember this",
                },
                "mcp": {
                    "url": "http://localhost/mcp",
                    "server_name": "lemma_tools",
                    "conversation_id": "conversation-claude-session",
                    "authorization": "Bearer claude-token",
                    "token": "claude-token",
                },
            },
        },
    )

    events = [message["event"] for message in websocket.messages]
    assert [event["type"] for event in events] == [
        "status",
        "status",
        "token",
        "message",
        "completed",
    ]
    assert events[1]["data"] == {
        "status": "daemon.session.started",
        "local_session": {
            "harness_kind": "CLAUDE_CODE",
            "session_id": "claude-session-1",
        },
    }


@pytest.mark.asyncio
async def test_daemon_recovers_from_stale_claude_session(monkeypatch, tmp_path):
    websocket = _FakeWebSocket()
    script_path = tmp_path / "claude_resume.py"
    script_path.write_text(
        textwrap.dedent(
            """
            import json
            import sys
            if "--resume" in sys.argv:
                print("session not found: stale-claude-session", file=sys.stderr)
                raise SystemExit(1)
            print(json.dumps({"type": "system", "session_id": "new-claude-session"}))
            print(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Recovered Claude."}
            ]}}))
            print(json.dumps({"type": "result", "result": "Recovered Claude."}))
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "LEMMA_DAEMON_CLAUDE_CODE_COMMAND",
        f"{sys.executable} {script_path}",
    )

    await daemon.handle_run_start(
        websocket,
        {
            "type": "run.start",
            "agent_run_id": "run-claude-stale",
            "payload": {
                "harness_kind": "CLAUDE_CODE",
                "model_name": "sonnet",
                "prompt": {
                    "session_id": "stale-claude-session",
                    "user_prompt": "continue",
                },
                "mcp": {
                    "url": "http://localhost/mcp",
                    "server_name": "lemma_tools",
                    "conversation_id": "conversation-claude-stale",
                    "authorization": "Bearer claude-token",
                    "token": "claude-token",
                },
            },
        },
    )

    events = [message["event"] for message in websocket.messages]
    statuses = [event["data"] for event in events if event["type"] == "status"]
    assert statuses[1:] == [
        {
            "status": "daemon.session.invalid",
            "local_session": {
                "harness_kind": "CLAUDE_CODE",
                "session_id": "stale-claude-session",
            },
        },
        {
            "status": "daemon.session.started",
            "local_session": {
                "harness_kind": "CLAUDE_CODE",
                "session_id": "new-claude-session",
            },
        },
    ]
    assert events[-1]["type"] == "completed"


@pytest.mark.asyncio
async def test_stream_text_state_persists_nonfinal_and_final_snapshots():
    emitted: list[tuple[str, object]] = []
    state = daemon._StreamTextState(
        harness_kind="OPENCODE",
        event_sink=lambda event_type, data: _capture_event(emitted, event_type, data),
    )

    await state.update_text_snapshot("Checking tools.")
    await state.flush(is_final=False)
    await state.update_text_snapshot("Done.")
    await state.flush(is_final=True)

    messages = [data for event_type, data in emitted if event_type == "message"]
    assert messages[0]["kind"] == "text"
    assert messages[0]["text"] == "Checking tools."
    assert messages[0]["metadata"]["is_final_answer"] is False
    assert messages[1]["kind"] == "text"
    assert messages[1]["text"] == "Done."
    assert "is_final_answer" not in messages[1]["metadata"]


@pytest.mark.asyncio
async def test_daemon_provider_command_runs_in_harness_cwd(monkeypatch, tmp_path):
    cwd = tmp_path / "claude-cwd"
    monkeypatch.setenv("LEMMA_DAEMON_CLAUDE_CODE_CWD", str(cwd))
    monkeypatch.setenv(
        "LEMMA_DAEMON_CLAUDE_CODE_COMMAND",
        f"{sys.executable} -c \"import pathlib; print(pathlib.Path.cwd())\"",
    )

    result = await daemon.run_provider_command(
        {
            "harness_kind": "CLAUDE_CODE",
            "model_name": "default",
            "prompt": {
                "system_prompt": "system",
                "user_prompt": "hello",
            },
            "mcp": {
                "url": "http://localhost/mcp",
                "server_name": "lemma_tools",
                "conversation_id": "conversation-cwd",
                "authorization": "Bearer cwd-token",
                "token": "cwd-token",
            },
        }
    )

    assert result["returncode"] == 0
    assert result["cwd"] == str(cwd)
    assert result["stdout"] == str(cwd)
    assert cwd.exists()


def test_provider_cwd_for_run_uses_conversation_scoped_scratch(monkeypatch, tmp_path):
    monkeypatch.setattr(
        daemon,
        "provider_cwd",
        lambda _harness_kind: tmp_path / "codex",
    )

    cwd = daemon.provider_cwd_for_run(
        "CODEX",
        {
            "conversation_id": "conversation-cwd",
            "workspace": {"cwd": "/workspace/conversations/conversation-cwd"},
        },
    )

    assert cwd == tmp_path / "codex" / "conversations" / "conversation-cwd"
    assert cwd.exists()


def test_provider_cwd_for_run_does_not_treat_workspace_cwd_as_host_cwd(
    monkeypatch,
    tmp_path,
):
    workspace_cwd = tmp_path / "workspace-from-metadata"
    workspace_cwd.mkdir()
    monkeypatch.setattr(
        daemon,
        "provider_cwd",
        lambda _harness_kind: tmp_path / "codex",
    )

    cwd = daemon.provider_cwd_for_run(
        "CODEX",
        {
            "conversation_id": "conversation-cwd",
            "workspace": {"cwd": str(workspace_cwd)},
        },
    )

    assert cwd == tmp_path / "codex" / "conversations" / "conversation-cwd"


def test_codex_default_command_uses_app_server_with_mcp_config(monkeypatch):
    monkeypatch.delenv("LEMMA_DAEMON_ENABLE_PROVIDER_NATIVE_TOOLS", raising=False)

    command = daemon.provider_command(
        harness_kind="CODEX",
        model_name="gpt-5.5",
        prompt_text="hello",
        mcp={
            "url": "http://localhost/mcp",
            "server_name": "lemma_tools",
            "authorization": "Bearer token-1",
            "token": "token-1",
            "tool_names": ["lemma_exec_command"],
        },
    )

    assert command[:2] == ["codex", "app-server"]
    assert "-c" in command
    config_arg = command[command.index("-c") + 1]
    assert config_arg.startswith("mcp_servers.lemma_tools=")
    assert 'url = "http://localhost/mcp"' in config_arg
    assert 'enabled_tools = ["lemma_exec_command"]' in config_arg
    assert "features.shell_tool=false" in command
    assert "features.unified_exec=false" in command
    assert "apps._default.enabled=false" in command
    assert "apps.imagegen.enabled=true" in command
    assert "features.multi_agent=false" in command
    assert 'web_search="disabled"' not in command
    assert "tools.view_image=false" not in command


def test_provider_native_tools_can_be_enabled_for_codex(monkeypatch):
    monkeypatch.setenv("LEMMA_DAEMON_ENABLE_PROVIDER_NATIVE_TOOLS", "1")

    command = daemon.provider_command(
        harness_kind="CODEX",
        model_name="gpt-5.5",
        prompt_text="hello",
        mcp={
            "url": "http://localhost/mcp",
            "server_name": "lemma_tools",
            "authorization": "Bearer token-1",
            "token": "token-1",
            "tool_names": ["lemma_exec_command"],
        },
    )

    assert "features.shell_tool=false" not in command
    assert "apps._default.enabled=false" not in command


def test_claude_mcp_args_disable_native_tools_by_default(monkeypatch):
    monkeypatch.delenv("LEMMA_DAEMON_ENABLE_PROVIDER_NATIVE_TOOLS", raising=False)

    command = daemon.provider_command(
        harness_kind="CLAUDE_CODE",
        model_name="claude-sonnet-4-5",
        prompt_text="hello",
        mcp={
            "url": "http://localhost/mcp",
            "server_name": "lemma_tools",
            "authorization": "Bearer token-1",
            "token": "token-1",
            "tool_names": ["lemma_exec_command"],
        },
    )

    assert "--tools" not in command
    assert "--allowedTools" in command
    assert "mcp__lemma_tools__lemma_exec_command" in command[command.index("--allowedTools") + 1]
    assert "--disallowedTools" in command
    disallowed_tools = command[command.index("--disallowedTools") + 1].split(",")
    assert "Bash" in disallowed_tools
    assert "Read" in disallowed_tools
    assert "WebSearch" not in disallowed_tools
    # Stale names removed in Claude Code 2.x must not return: each one Claude Code
    # doesn't recognize prints a "matches no known tool" warning on every run.
    for stale in ("LS", "MultiEdit", "NotebookRead", "TodoRead"):
        assert stale not in disallowed_tools


def test_claude_command_resumes_saved_session():
    command = daemon.provider_command(
        harness_kind="CLAUDE_CODE",
        model_name="claude-sonnet-4-5",
        prompt_text="hello",
        session_id="claude-session-1",
        mcp={},
    )

    assert command[-2:] == ["--resume", "claude-session-1"]


def test_opencode_server_environment_injects_mcp_config(monkeypatch):
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    monkeypatch.delenv("LEMMA_DAEMON_ENABLE_PROVIDER_NATIVE_TOOLS", raising=False)

    env = daemon.provider_environment(
        harness_kind="OPENCODE",
        mcp={
            "url": "http://localhost/mcp",
            "server_name": "lemma_tools",
            "authorization": "Bearer token-1",
            "token": "token-1",
            "tool_names": ["lemma_exec_command"],
        },
    )

    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert config["mcp"]["lemma_tools"]["url"] == "http://localhost/mcp"
    assert config["mcp"]["lemma_tools"]["oauth"] is False
    assert config["tools"]["lemma_exec_command"] is True
    assert config["tools"]["lemma_tools_lemma_exec_command"] is True
    assert config["tools"]["bash"] is False
    assert config["tools"]["edit"] is False
    assert "websearch" not in config["tools"]
    assert "webfetch" not in config["tools"]
    assert config["permission"]["bash"] == "deny"
    assert config["permission"]["edit"] == "deny"


@pytest.mark.asyncio
async def test_opencode_turn_uses_saved_session_without_creating_new_one(monkeypatch, tmp_path):
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    prompt_submitted = {"value": False}

    async def fake_opencode_request(
        client: object,
        method: str,
        base_url: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> object:
        del client, base_url, params
        calls.append((method, path, body))
        if path == "/mcp/lemma_tools/connect":
            return {}
        if path == "/session":
            raise AssertionError("saved OpenCode sessions should not create a new session")
        if path == "/session/opencode-session-1/prompt_async":
            prompt_submitted["value"] = True
            return {}
        if path == "/session/opencode-session-1/message":
            # Before this turn's prompt, the resumed session holds the prior
            # turn's reply (the baseline); the new reply only appears afterwards.
            prior = {
                "role": "assistant",
                "parts": [{"type": "text", "text": "Earlier OpenCode reply."}],
            }
            if not prompt_submitted["value"]:
                return [prior]
            return [
                prior,
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "text": "Resumed OpenCode."}],
                },
            ]
        if path == "/session/status":
            return {}
        raise AssertionError(f"unexpected OpenCode request: {method} {path}")

    async def ignore_permissions(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(daemon, "_opencode_request", fake_opencode_request)
    monkeypatch.setattr(daemon, "_accept_lemma_opencode_permissions", ignore_permissions)

    output = await daemon._run_opencode_turn(
        base_url="http://127.0.0.1:1234",
        cwd=tmp_path,
        model_name="default",
        prompt_text="continue",
        session_id="opencode-session-1",
        mcp={
            "server_name": "lemma_tools",
            "url": "http://localhost/mcp",
            "authorization": "Bearer token",
        },
    )

    assert output == "Resumed OpenCode."
    assert ("POST", "/session", None) not in calls
    assert (
        "POST",
        "/session/opencode-session-1/prompt_async",
        {"parts": [{"type": "text", "text": "continue"}]},
    ) in calls


@pytest.mark.asyncio
async def test_opencode_turn_recovers_from_stale_saved_session(monkeypatch, tmp_path):
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    emitted: list[tuple[str, object]] = []

    async def fake_opencode_request(
        client: object,
        method: str,
        base_url: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> object:
        del client, base_url, params
        calls.append((method, path, body))
        if path == "/mcp/lemma_tools/connect":
            return {}
        if path == "/session/opencode-stale/message":
            # Baseline fetch on the stale session before the prompt is submitted.
            return []
        if path == "/session/opencode-stale/prompt_async":
            raise RuntimeError(
                "OpenCode POST /session/opencode-stale/prompt_async failed: 404 not found"
            )
        if path == "/session":
            return {"id": "opencode-new"}
        if path == "/session/opencode-new/prompt_async":
            return {}
        if path == "/session/opencode-new/message":
            return [
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "text": "Recovered OpenCode."}],
                }
            ]
        if path == "/session/status":
            return {}
        raise AssertionError(f"unexpected OpenCode request: {method} {path}")

    async def ignore_permissions(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(daemon, "_opencode_request", fake_opencode_request)
    monkeypatch.setattr(daemon, "_accept_lemma_opencode_permissions", ignore_permissions)

    output = await daemon._run_opencode_turn(
        base_url="http://127.0.0.1:1234",
        cwd=tmp_path,
        model_name="default",
        prompt_text="continue",
        session_id="opencode-stale",
        mcp={
            "server_name": "lemma_tools",
            "url": "http://localhost/mcp",
            "authorization": "Bearer token",
        },
        event_sink=lambda event_type, data: _capture_event(emitted, event_type, data),
    )

    assert output == "Recovered OpenCode."
    assert [data for event_type, data in emitted if event_type == "status"] == [
        {
            "status": "daemon.session.invalid",
            "local_session": {
                "harness_kind": "OPENCODE",
                "session_id": "opencode-stale",
            },
        },
        {
            "status": "daemon.session.started",
            "local_session": {
                "harness_kind": "OPENCODE",
                "session_id": "opencode-new",
            },
        },
    ]
    assert (
        "POST",
        "/session/opencode-new/prompt_async",
        {"parts": [{"type": "text", "text": "continue"}]},
    ) in calls


def test_daemon_log_redaction_scrubs_bearer_tokens_inside_strings():
    value = daemon._redact(
        [
            "claude",
            "--mcp-config",
            '{"headers":{"Authorization":"Bearer bridge-secret-token"}}',
        ]
    )

    assert "bridge-secret-token" not in json.dumps(value)
    assert "Bearer <redacted>" in json.dumps(value)


def test_daemon_log_compacts_payloads_unless_debug_enabled(monkeypatch):
    output: list[str] = []
    monkeypatch.delenv("LEMMA_DAEMON_DEBUG", raising=False)
    monkeypatch.setattr(daemon.console, "print", lambda value: output.append(str(value)))

    daemon._set_daemon_debug(False)
    daemon._daemon_log(
        "incoming websocket message",
        {"type": "run.start", "payload": {"token": "secret-token"}},
    )

    assert output == ["[daemon] incoming websocket message: run.start"]
    assert "secret-token" not in output[0]

    output.clear()
    daemon._set_daemon_debug(True)
    daemon._daemon_log(
        "incoming websocket message",
        {"type": "run.start", "payload": {"token": "secret-token"}},
    )

    assert output[0].startswith("[daemon] incoming websocket message: ")
    assert "run.start" in output[0]
    assert "secret-token" not in output[0]
    assert "<redacted>" in output[0]
    daemon._set_daemon_debug(False)


def test_daemon_rewrites_upstream_mcp_url_to_connected_backend_base_url():
    payload = {
        "mcp": {
            "url": "http://localhost:8711/agent-runtime/conversations/conversation-1/mcp",
        }
    }

    rewritten = daemon._payload_with_reachable_mcp_urls(
        payload,
        base_url="http://127.0.0.1:58021",
    )

    assert rewritten["mcp"]["url"] == (
        "http://127.0.0.1:58021/agent-runtime/conversations/conversation-1/mcp"
    )


def test_discover_harness_catalog_uses_real_cli_model_commands(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "codex",
        f"""\
        #!{sys.executable}
        import json
        import sys

        if sys.argv[1:] == ["debug", "models"]:
            print(json.dumps({{"models": [
                {{"slug": "gpt-5.5"}},
                {{"slug": "gpt-5.5-mini"}},
            ]}}))
            raise SystemExit(0)
        if "--version" in sys.argv:
            print("codex fake")
            raise SystemExit(0)
        """,
    )
    _write_executable(
        bin_dir / "opencode",
        f"""\
        #!{sys.executable}
        import sys

        if sys.argv[1:] == ["models"]:
            print("openai/gpt-5.5")
            print("anthropic/claude-sonnet-4-5")
            raise SystemExit(0)
        if "--version" in sys.argv:
            print("opencode fake")
            raise SystemExit(0)
        """,
    )
    _write_executable(
        bin_dir / "claude",
        f"""\
        #!{sys.executable}
        import sys

        if "--help" in sys.argv:
            print("--model <model> alias 'sonnet' or 'opus'")
            raise SystemExit(0)
        if "--version" in sys.argv:
            print("claude fake")
            raise SystemExit(0)
        """,
    )
    monkeypatch.setenv("PATH", str(bin_dir))

    catalog = daemon.discover_harness_catalog()

    assert catalog["CODEX"]["models"] == ["gpt-5.5", "gpt-5.5-mini"]
    assert catalog["OPENCODE"]["models"] == [
        "openai/gpt-5.5",
        "anthropic/claude-sonnet-4-5",
    ]
    assert catalog["CLAUDE_CODE"]["models"] == ["sonnet", "opus"]
    # Claude Code aliases are advertised with full standard-context model ids so
    # the default path never opts into the paid 1M-context variant.
    claude_catalog = catalog["CLAUDE_CODE"]["model_catalog"]
    by_name = {entry["name"]: entry for entry in claude_catalog}
    assert by_name["sonnet"]["provider_model_name"] == "claude-sonnet-4-6"
    assert by_name["sonnet"]["display_name"] == "Claude Sonnet 4.6"
    assert by_name["sonnet"]["metadata"]["context_window"] == "standard"
    assert by_name["opus"]["provider_model_name"] == "claude-opus-4-8"
    # Other harnesses keep selection name == provider model name.
    codex_catalog = {entry["name"]: entry for entry in catalog["CODEX"]["model_catalog"]}
    assert codex_catalog["gpt-5.5"]["provider_model_name"] == "gpt-5.5"


def test_discover_harness_models_allows_explicit_override(monkeypatch):
    monkeypatch.setenv("LEMMA_DAEMON_CODEX_MODELS", '["gpt-5.5", "gpt-5.4"]')

    assert daemon.discover_harness_models("CODEX", "codex") == (
        ["gpt-5.5", "gpt-5.4"],
        None,
    )


def test_order_opencode_models_pushes_free_tier_last():
    from lemma_cli.daemon.catalog import _order_opencode_models

    ordered = _order_opencode_models(
        [
            "opencode/deepseek-v4-flash-free",
            "fireworks-ai/accounts/fireworks/models/deepseek-v4-flash",
            "opencode/mimo-v2.5-free",
            "fireworks-ai/accounts/fireworks/models/glm-5p1",
        ]
    )
    # Reliable provider models first (stable order), flaky *-free tier last, so
    # the default selection (first model) doesn't land on a rate-limited model.
    assert ordered == [
        "fireworks-ai/accounts/fireworks/models/deepseek-v4-flash",
        "fireworks-ai/accounts/fireworks/models/glm-5p1",
        "opencode/deepseek-v4-flash-free",
        "opencode/mimo-v2.5-free",
    ]


def test_seed_opencode_auth_copies_user_credentials(tmp_path, monkeypatch):
    from lemma_cli.daemon.mcp import _seed_opencode_auth

    source_home = tmp_path / "user-share"
    (source_home / "opencode").mkdir(parents=True)
    (source_home / "opencode" / "auth.json").write_text('{"fireworks-ai":"creds"}', encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(source_home))

    data_home = tmp_path / "daemon-data"
    _seed_opencode_auth(str(data_home))

    seeded = data_home / "opencode" / "auth.json"
    assert seeded.is_file()
    assert seeded.read_text(encoding="utf-8") == '{"fireworks-ai":"creds"}'


def test_seed_opencode_auth_noop_without_source(tmp_path, monkeypatch):
    from lemma_cli.daemon.mcp import _seed_opencode_auth

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty-home"))
    data_home = tmp_path / "daemon-data"
    # No source auth.json -> no-op, and must never raise.
    _seed_opencode_auth(str(data_home))
    assert not (data_home / "opencode" / "auth.json").exists()


def test_normalize_provider_model_name_maps_claude_aliases():
    # Bare aliases resolve to standard-context full ids.
    assert daemon.normalize_provider_model_name("CLAUDE_CODE", "sonnet") == "claude-sonnet-4-6"
    assert daemon.normalize_provider_model_name("CLAUDE_CODE", " opus ") == "claude-opus-4-8"
    # Full ids, "default", and unknown names pass through untouched.
    assert daemon.normalize_provider_model_name("CLAUDE_CODE", "claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert daemon.normalize_provider_model_name("CLAUDE_CODE", "default") == "default"
    # Aliases only get rewritten for Claude Code, not other harnesses.
    assert daemon.normalize_provider_model_name("OPENCODE", "sonnet") == "sonnet"


def test_claude_command_normalizes_alias_to_standard_context_model():
    command = daemon.provider_command(
        harness_kind="CLAUDE_CODE",
        model_name="sonnet",
        prompt_text="hello",
        mcp={},
    )

    assert "--model" in command
    assert command[command.index("--model") + 1] == "claude-sonnet-4-6"
    assert "sonnet" not in command


def test_cursor_command_uses_stream_json_headless():
    command = daemon.provider_command(
        harness_kind="CURSOR",
        model_name="auto",
        prompt_text="hello",
        mcp={},
    )
    assert command[:2] == ["cursor-agent", "-p"]
    assert command[command.index("--model") + 1] == "auto"
    assert "--output-format" in command and command[command.index("--output-format") + 1] == "stream-json"
    assert {"--trust", "--force", "--approve-mcps"} <= set(command)


def test_antigravity_command_runs_headless_one_shot():
    command = daemon.provider_command(
        harness_kind="ANTIGRAVITY",
        model_name="Gemini 3.5 Flash (Low)",
        prompt_text="hello",
        mcp={},
    )
    assert command[:2] == ["agy", "-p"]
    assert command[command.index("--model") + 1] == "Gemini 3.5 Flash (Low)"
    assert "--dangerously-skip-permissions" in command


def test_write_provider_mcp_files_cursor_and_antigravity(tmp_path):
    from lemma_cli.daemon.mcp import write_provider_mcp_files

    mcp = {
        "url": "https://api.lemma.work/mcp",
        "server_name": "lemma_tools",
        "token": "tok-1",
    }
    cursor_cwd = tmp_path / "cursor"
    cursor_cwd.mkdir()
    write_provider_mcp_files("CURSOR", cursor_cwd, mcp)
    cursor_config = json.loads((cursor_cwd / ".cursor" / "mcp.json").read_text())
    server = cursor_config["mcpServers"]["lemma_tools"]
    assert server["url"] == "https://api.lemma.work/mcp"
    assert server["headers"]["Authorization"] == "Bearer tok-1"

    agy_cwd = tmp_path / "agy"
    agy_cwd.mkdir()
    write_provider_mcp_files("ANTIGRAVITY", agy_cwd, mcp)
    agy_config = json.loads((agy_cwd / ".agents" / "mcp_config.json").read_text())
    agy_server = agy_config["mcpServers"]["lemma_tools"]
    # Antigravity requires serverUrl (not url/httpUrl) for remote servers.
    assert agy_server["serverUrl"] == "https://api.lemma.work/mcp"
    assert "url" not in agy_server
    assert agy_server["headers"]["Authorization"] == "Bearer tok-1"


def test_write_provider_mcp_files_noop_without_usable_mcp(tmp_path):
    from lemma_cli.daemon.mcp import write_provider_mcp_files

    write_provider_mcp_files("CURSOR", tmp_path, {})
    assert not (tmp_path / ".cursor").exists()


def test_discover_cursor_model_entries_parses_id_label(tmp_path, monkeypatch):
    from lemma_cli.daemon.catalog import discover_cursor_model_entries

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "cursor-agent",
        f"""\
        #!{sys.executable}
        print("Available models")
        print("")
        print("auto - Auto (current)")
        print("gpt-5.3-codex-low - Codex 5.3 Low")
        """,
    )
    monkeypatch.setenv("PATH", str(bin_dir))

    entries = discover_cursor_model_entries("cursor-agent")
    by_name = {e["name"]: e for e in entries}
    assert by_name["auto"]["display_name"] == "Auto"  # "(current)" stripped
    assert by_name["auto"]["provider_model_name"] == "auto"
    assert by_name["gpt-5.3-codex-low"]["display_name"] == "Codex 5.3 Low"


def test_discover_antigravity_model_entries_uses_display_names(tmp_path, monkeypatch):
    from lemma_cli.daemon.catalog import discover_antigravity_model_entries

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "agy",
        f"""\
        #!{sys.executable}
        print("Gemini 3.5 Flash (Low)")
        print("Claude Sonnet 4.6 (Thinking)")
        """,
    )
    monkeypatch.setenv("PATH", str(bin_dir))

    entries = discover_antigravity_model_entries("agy")
    names = [e["name"] for e in entries]
    assert "Gemini 3.5 Flash (Low)" in names
    # Antigravity accepts the display name as --model, so all three fields match.
    claude = next(e for e in entries if e["name"].startswith("Claude"))
    assert claude["display_name"] == claude["provider_model_name"] == "Claude Sonnet 4.6 (Thinking)"


def _write_executable(path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    path.chmod(0o755)


async def _capture_event(
    emitted: list[tuple[str, object]],
    event_type: str,
    data: object,
) -> None:
    emitted.append((event_type, data))


class _FakeCodexJsonRpcProcess:
    instances: list["_FakeCodexJsonRpcProcess"] = []
    next_thread_id = 0

    def __init__(self, command: list[str], *, cwd: Path, env: dict[str, str]):
        self.command = command
        self.cwd = cwd
        self.env = env
        self.requests: list[tuple[str, dict[str, Any] | None]] = []
        self.notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.server_requests: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.stderr_lines: list[str] = []
        self.closed = False
        self.__class__.instances.append(self)

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True
        return None

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        return None

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        del timeout
        self.requests.append((method, params))
        if method == "thread/start":
            self.__class__.next_thread_id += 1
            return {"thread": {"id": f"thread-{self.__class__.next_thread_id}"}}
        if method == "turn/start":
            for item in _fake_codex_notifications():
                self.notifications.put_nowait(item)
            return {"turn": {"id": "turn-1"}}
        return {}

    async def respond(self, request_id: object, result: dict[str, Any]) -> None:
        return None

    async def respond_error(self, request_id: object, message: str) -> None:
        return None

    def is_alive(self) -> bool:
        return True


class _SlowFakeCodexJsonRpcProcess(_FakeCodexJsonRpcProcess):
    active_turns = 0
    max_active_turns = 0
    instances: list["_SlowFakeCodexJsonRpcProcess"] = []

    def __init__(self, command: list[str], *, cwd: Path, env: dict[str, str]):
        super().__init__(command, cwd=cwd, env=env)

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        if method != "turn/start":
            return await super().request(method, params, timeout=timeout)
        self.__class__.active_turns += 1
        self.__class__.max_active_turns = max(
            self.__class__.max_active_turns,
            self.__class__.active_turns,
        )
        try:
            await asyncio.sleep(0.05)
            self.notifications.put_nowait(
                {"method": "item/outputText/delta", "params": {"delta": "hi"}}
            )
            self.notifications.put_nowait(
                {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}
            )
            return {"turn": {"id": "turn-slow"}}
        finally:
            self.__class__.active_turns -= 1


class _HangingFakeCodexJsonRpcProcess(_FakeCodexJsonRpcProcess):
    instances: list["_HangingFakeCodexJsonRpcProcess"] = []

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        if method != "turn/start":
            return await super().request(method, params, timeout=timeout)
        self.requests.append((method, params))
        return {"turn": {"id": "turn-hanging"}}


class _FailingTurnFakeCodexJsonRpcProcess(_FakeCodexJsonRpcProcess):
    instances: list["_FailingTurnFakeCodexJsonRpcProcess"] = []

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        if method == "turn/start" and (params or {}).get("threadId") == "thread-expired":
            self.requests.append((method, params))
            self.stderr_lines.append("codex stderr detail")
            raise daemon._JsonRpcRequestError(
                method=method,
                error={
                    "code": -32600,
                    "message": "thread not found: thread-expired",
                },
                stderr_tail="\n".join(self.stderr_lines[-20:]),
            )
        return await super().request(method, params, timeout=timeout)


class _MultiMessageFakeCodexJsonRpcProcess(_FakeCodexJsonRpcProcess):
    instances: list["_MultiMessageFakeCodexJsonRpcProcess"] = []

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        if method != "turn/start":
            return await super().request(method, params, timeout=timeout)
        self.requests.append((method, params))
        for item in _fake_codex_multi_message_notifications():
            self.notifications.put_nowait(item)
        return {"turn": {"id": "turn-multi-message"}}


class _PromptEchoFakeCodexJsonRpcProcess(_FakeCodexJsonRpcProcess):
    instances: list["_PromptEchoFakeCodexJsonRpcProcess"] = []

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        if method != "turn/start":
            return await super().request(method, params, timeout=timeout)
        self.requests.append((method, params))
        prompt_text = str((params or {})["input"][0]["text"])
        self.notifications.put_nowait(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "id": "prompt-echo",
                        "type": "agentMessage",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": f"{prompt_text}\n\nassistant clean",
                            }
                        ],
                    }
                },
            }
        )
        self.notifications.put_nowait(
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}
        )
        return {"turn": {"id": "turn-prompt-echo"}}


def _fake_codex_notifications() -> list[dict[str, Any]]:
    return [
        {"method": "item/outputText/delta", "params": {"delta": "Intro "}},
        {
            "method": "item/started",
            "params": {
                "item": {
                    "id": "call-1",
                    "type": "mcpToolCall",
                    "server": "lemma_tools",
                    "tool": "lemma_exec_command",
                    "arguments": {"cmd": "printf OK"},
                }
            },
        },
        {
            "method": "item/outputText/delta",
            "params": {
                "itemId": "call-1",
                "delta": '{\n  "context": "default",\n  "source": "config"\n}\n',
            },
        },
        {
            "method": "item/commandExecution/outputDelta",
            "params": {
                "itemId": "cmd-1",
                "delta": '{\n  "context": "default",\n  "source": "command"\n}\n',
            },
        },
        {"method": "item/outputText/delta", "params": {"delta": "Before "}},
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "id": "call-1",
                    "type": "mcpToolCall",
                    "server": "lemma_tools",
                    "tool": "lemma_exec_command",
                    "arguments": {"cmd": "printf OK"},
                    "result": {"structuredContent": {"stdout": "OK"}},
                }
            },
        },
        {"method": "item/outputText/delta", "params": {"delta": "After"}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ]


def _fake_codex_multi_message_notifications() -> list[dict[str, Any]]:
    return [
        {
            "method": "item/agentMessage/delta",
            "params": {"itemId": "msg-1", "delta": "First durable "},
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"itemId": "msg-1", "delta": "assistant message."},
        },
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "id": "msg-1",
                    "type": "agentMessage",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "First durable assistant message.",
                        }
                    ],
                }
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"itemId": "msg-2", "delta": "Second durable "},
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"itemId": "msg-2", "delta": "assistant message."},
        },
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "id": "msg-2",
                    "type": "agentMessage",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Second durable assistant message.",
                        }
                    ],
                }
            },
        },
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ]
