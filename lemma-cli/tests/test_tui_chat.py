"""Pilot tests for the reworked TUI: event rendering and screen behavior.

Network is never touched: `load_rows` and picker loaders are monkeypatched, and
chat rendering is exercised by feeding canned events into ChatScreen.handle_event.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


from lemma_cli.cli_core.chat import StreamEvent
from lemma_cli.tui import data as tui_data
from lemma_cli.tui.app import LemmaTuiApp
from lemma_cli.tui.events import (
    ErrorEvent,
    Terminal,
    TextMessage,
    Thinking,
    Token,
    ToolCall,
    ToolReturn,
    Usage,
    normalize_event,
)
from lemma_cli.tui.screens.chat import ChatScreen
from lemma_cli.tui.widgets.messages import (
    AgentMessage,
    ApprovalPrompt,
    StatusLine,
    ThinkingBlock,
    ToolCallWidget,
    UserMessage,
)


def _write_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "active_server": "lemma-cloud",
                "servers": {
                    "lemma-cloud": {
                        "base_url": "http://localhost:8711",
                        "defaults": {"org_id": "org-1", "pod_id": "pod-1"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return config_file


def _app(tmp_path: Path, **kwargs) -> LemmaTuiApp:
    return LemmaTuiApp(config_file=_write_config(tmp_path), **kwargs)


# ---------------------------------------------------------------- events


def test_normalize_token_and_terminal():
    assert normalize_event(StreamEvent(type="token", data="hi")) == Token("hi")
    terminal = normalize_event(
        StreamEvent(type="completed", data={"status": "completed"})
    )
    assert terminal == Terminal(kind="completed", detail="completed")


def test_normalize_tool_call_and_return():
    call = normalize_event(
        StreamEvent(
            type="message",
            data={
                "role": "assistant",
                "kind": "tool_call",
                "tool_name": "web_search",
                "tool_call_id": "call_1",
                "tool_args": {"query": "lemma"},
            },
        )
    )
    assert call == ToolCall(
        tool_name="web_search", tool_call_id="call_1", tool_input={"query": "lemma"}
    )

    ret = normalize_event(
        StreamEvent(
            type="message",
            data={
                "role": "tool",
                "kind": "tool_return",
                "tool_name": "web_search",
                "tool_call_id": "call_1",
                "tool_result": {"results": []},
            },
        )
    )
    assert ret == ToolReturn(
        tool_name="web_search", tool_call_id="call_1", result={"results": []}
    )


def test_normalize_thinking_and_usage():
    thinking = normalize_event(
        StreamEvent(
            type="message",
            data={"role": "assistant", "kind": "thinking", "text": "hmm"},
        )
    )
    assert thinking == Thinking("hmm")
    usage = normalize_event(
        StreamEvent(type="usage", data={"input_tokens": 10, "output_tokens": 5})
    )
    assert usage == Usage(input_tokens=10, output_tokens=5, tool_call_count=0)


# ----------------------------------------------------------------- chat UI


@pytest.mark.asyncio
async def test_chat_screen_renders_stream(tmp_path):
    app = _app(tmp_path, agent="triage")
    async with app.run_test() as pilot:
        screen = ChatScreen(state=app.state, agent="triage")
        await app.push_screen(screen)
        await pilot.pause()

        screen.handle_event(TextMessage("hello", role="user"))
        screen.handle_event(Token("Working"))
        screen.handle_event(Token(" on it"))
        screen.handle_event(
            ToolCall(tool_name="web_search", tool_call_id="c1", tool_input={"q": "x"})
        )
        screen.handle_event(
            ToolReturn(tool_name="web_search", tool_call_id="c1", result={"hits": 3})
        )
        screen.handle_event(Thinking("considering results"))
        screen.handle_event(Usage(input_tokens=12, output_tokens=7, tool_call_count=1))
        screen.handle_event(Terminal(kind="completed"))
        await pilot.pause()

        assert len(screen.query(UserMessage)) == 1
        agent_messages = screen.query(AgentMessage)
        assert len(agent_messages) == 1
        assert agent_messages.first().text == "Working on it"

        tools = screen.query(ToolCallWidget)
        assert len(tools) == 1
        assert tools.first().title == "✓ web_search"
        assert len(screen.query(ThinkingBlock)) == 1
        assert any(
            "completed" in str(line.render()) for line in screen.query(StatusLine)
        )


@pytest.mark.asyncio
async def test_chat_screen_pairs_tool_output_by_call_id(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        screen = ChatScreen(state=app.state, agent=None)
        await app.push_screen(screen)
        await pilot.pause()

        screen.handle_event(ToolCall(tool_name="a", tool_call_id="c1", tool_input={}))
        screen.handle_event(ToolCall(tool_name="b", tool_call_id="c2", tool_input={}))
        screen.handle_event(ToolReturn(tool_name="b", tool_call_id="c2", result="B"))
        await pilot.pause()

        tools = list(screen.query(ToolCallWidget))
        assert tools[0].title == "⚒ a"  # still running
        assert tools[1].title == "✓ b"


@pytest.mark.asyncio
async def test_chat_screen_error_renders_status(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        screen = ChatScreen(state=app.state, agent=None)
        await app.push_screen(screen)
        await pilot.pause()

        screen.handle_event(ErrorEvent("boom"))
        await pilot.pause()
        assert any("boom" in str(line.render()) for line in screen.query(StatusLine))


# ----------------------------------------------------------------- approvals


def test_normalize_tool_return_reads_tool_result():
    ret = normalize_event(
        StreamEvent(
            type="message",
            data={
                "role": "tool",
                "kind": "tool_return",
                "tool_name": "request_approval",
                "tool_call_id": "appr_1",
                "tool_result": {"decision": "APPROVE_ONCE", "response": {}},
            },
        )
    )
    assert ret == ToolReturn(
        tool_name="request_approval",
        tool_call_id="appr_1",
        result={"decision": "APPROVE_ONCE", "response": {}},
    )


@pytest.mark.asyncio
async def test_chat_screen_renders_approval_and_stream_resolution(tmp_path):
    app = _app(tmp_path, agent="triage")
    async with app.run_test() as pilot:
        screen = ChatScreen(state=app.state, agent="triage")
        await app.push_screen(screen)
        await pilot.pause()

        screen.handle_event(
            ToolCall(
                tool_name="request_approval",
                tool_call_id="appr_1",
                tool_input={
                    "tool_name": "exec_command",
                    "args": {"cmd": "deploy.sh"},
                    "title": "Run the deploy script?",
                    "reason": "Executes deploy.sh against production.",
                },
            )
        )
        await pilot.pause()

        prompts = screen.query(ApprovalPrompt)
        assert len(prompts) == 1
        assert prompts.first().approval_id == "appr_1"
        # approval calls render as a prompt card, not a generic tool call
        assert len(screen.query(ToolCallWidget)) == 0

        # duplicate stream/poll detection does not mount a second card
        screen.handle_event(
            ToolCall(tool_name="request_approval", tool_call_id="appr_1", tool_input={})
        )
        await pilot.pause()
        assert len(screen.query(ApprovalPrompt)) == 1

        # the resolved tool_return arriving on the stream marks the card resolved
        screen.handle_event(
            ToolReturn(
                tool_name="request_approval",
                tool_call_id="appr_1",
                result={"decision": "APPROVE_ONCE", "response": {}},
            )
        )
        await pilot.pause()
        prompt = prompts.first()
        assert prompt.resolved_decision == "APPROVE_ONCE"
        from textual.widgets import Button

        assert all(button.disabled for button in prompt.query(Button))


@pytest.mark.asyncio
async def test_approval_buttons_trigger_resolution(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        screen = ChatScreen(state=app.state, agent=None)
        screen.conversation_id = "conv-1"
        await app.push_screen(screen)
        await pilot.pause()

        resolutions = []
        screen.resolve_approval = lambda prompt, decision: resolutions.append(
            (prompt.approval_id, decision)
        )

        screen.handle_event(
            ToolCall(
                tool_name="request_approval",
                tool_call_id="appr_1",
                tool_input={"title": "Allow access?"},
            )
        )
        await pilot.pause()

        prompt = screen.query_one(ApprovalPrompt)
        from textual.widgets import Button

        prompt.query_one(".approval-deny", Button).press()
        await pilot.pause()

        assert resolutions == [("appr_1", "DENY")]
        assert all(button.disabled for button in prompt.query(Button))


@pytest.mark.asyncio
async def test_pending_approvals_mounted_from_poll_payload(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        screen = ChatScreen(state=app.state, agent=None)
        screen.conversation_id = "conv-1"
        await app.push_screen(screen)
        await pilot.pause()

        # shape of UserApprovalListResponse items (flat MessageResponse dicts)
        screen._mount_pending_approvals(
            [
                {
                    "role": "assistant",
                    "kind": "tool_call",
                    "tool_name": "request_approval",
                    "tool_call_id": "appr_9",
                    "tool_args": {
                        "tool_name": "read_file",
                        "title": "Read ~/.ssh?",
                        "reason": "Inspect SSH config",
                    },
                }
            ]
        )
        await pilot.pause()

        prompts = screen.query(ApprovalPrompt)
        assert len(prompts) == 1
        assert prompts.first().approval_id == "appr_9"


# ----------------------------------------------------------------- pod screen


@pytest.mark.asyncio
async def test_pod_screen_loads_rows_and_status(tmp_path, monkeypatch):
    rows = [{"name": "triage", "status": "ACTIVE", "kind": "ASSISTANT"}]
    monkeypatch.setattr(tui_data, "load_rows", lambda state, view: rows)
    # PodScreen imported load_rows by name
    from lemma_cli.tui.screens import pod as pod_screen_module

    monkeypatch.setattr(pod_screen_module, "load_rows", lambda state, view: rows)

    app = _app(tmp_path, agent="triage")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import DataTable

        table = app.screen.query_one("#resources", DataTable)
        for _ in range(20):
            if table.row_count:
                break
            await pilot.pause(0.05)
        assert table.row_count == 1

        from lemma_cli.tui.widgets.status_bar import StatusBar

        status = app.screen.query_one("#status-bar", StatusBar)
        text = str(status.render())
        assert "org-1" in text
        assert "pod-1" in text


@pytest.mark.asyncio
async def test_pod_screen_error_notifies(tmp_path, monkeypatch):
    from lemma_cli.tui.screens import pod as pod_screen_module

    def boom(state, view):
        raise ValueError("No pod selected. Press 'p' to pick a pod.")

    monkeypatch.setattr(pod_screen_module, "load_rows", boom)

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        from textual.widgets import DataTable

        table = app.screen.query_one("#resources", DataTable)
        assert table.row_count == 0


# ----------------------------------------------------------------- pickers


@pytest.mark.asyncio
async def test_server_picker_switches_server(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "active_server": "lemma-cloud",
                "servers": {
                    "lemma-cloud": {"defaults": {"org_id": "org-1", "pod_id": "pod-1"}},
                    "local": {
                        "base_url": "http://localhost:8711",
                        "defaults": {"org_id": "org-2", "pod_id": "pod-2"},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    app = LemmaTuiApp(config_file=config_file)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.pick_server()
        await pilot.pause()

        from lemma_cli.tui.screens.pickers import ServerPickerScreen

        assert isinstance(app.screen, ServerPickerScreen)
        from textual.widgets import OptionList

        options = app.screen.query_one("#picker-options", OptionList)
        options.highlighted = 1  # "local"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert app.state.server == "local"
        saved = json.loads(config_file.read_text(encoding="utf-8"))
        assert saved["active_server"] == "local"


@pytest.mark.asyncio
async def test_ctrl_c_quits_from_pod_screen(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
    assert app._exit is True or app.return_code is not None


@pytest.mark.asyncio
async def test_ctrl_c_quits_from_chat_screen_with_input_focused(tmp_path):
    app = _app(tmp_path, agent="triage")
    async with app.run_test() as pilot:
        screen = ChatScreen(state=app.state, agent="triage")
        await app.push_screen(screen)
        await pilot.pause()
        from textual.widgets import Input

        # Focus the chat input — Ctrl+C must still quit, not type into it.
        screen.query_one("#chat-input", Input).focus()
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
    assert app._exit is True or app.return_code is not None
