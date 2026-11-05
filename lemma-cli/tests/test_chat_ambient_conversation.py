"""`lemma chat <agent>` must reach that agent, not the caller's own conversation.

Inside an agent sandbox LEMMA_CONVERSATION_ID is the *calling* agent's
conversation. `lemma chat builder -m "…"` used to resolve it as the conversation
to post into, so the message landed in the caller's own conversation and the
caller answered it; the builder never saw it.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.context import chat_conversation

runner = CliRunner()

AMBIENT = "caller-conversation"
BUILDER_ID = "agent-builder"
OTHER_ID = "agent-caller"


class FakeConversations:
    def __init__(self, captured: dict[str, object], owners: dict[str, str]):
        self.captured = captured
        self.owners = owners

    def create(self, pod_id, payload):
        self.captured["create"] = payload
        return {"id": "new-conversation"}

    def get(self, pod_id, conversation_id):
        self.captured.setdefault("get", []).append(conversation_id)
        return {"id": conversation_id, "agent_id": self.owners.get(conversation_id)}

    def send_message(self, pod_id, conversation_id, *, content, stream):
        self.captured["send"] = (conversation_id, content)

        class Response:
            def iter_lines(self, decode_unicode=True):
                yield 'data: {"type":"token","data":"on it"}'
                yield ""
                yield 'data: {"type":"completed","data":{"status":"completed"}}'
                yield ""

            def close(self):
                return None

        return Response()


class FakeAgents:
    def get(self, pod_id, name):
        if name == "pod_default":
            return {"id": "pod-1", "name": name, "kind": "POD_DEFAULT"}
        return {"id": {"builder": BUILDER_ID}.get(name, f"agent-{name}"), "name": name}


@pytest.fixture
def captured(monkeypatch):
    captured: dict[str, object] = {}
    owners = {AMBIENT: OTHER_ID, "builder-conversation": BUILDER_ID}
    # "assistant-conversation" is absent: the pod assistant's records no agent.
    client = SimpleNamespace(
        conversations=FakeConversations(captured, owners), agents=FakeAgents()
    )

    @contextmanager
    def fake_session(state):
        yield client

    # The API client is the collaborator stood in for: the command, its
    # conversation choice and its ownership check all run as shipped.
    monkeypatch.setattr("lemma_cli.cli_core.state.client_session", fake_session)
    monkeypatch.setenv("LEMMA_CONVERSATION_ID", AMBIENT)
    return captured


def test_named_agent_ignores_the_ambient_conversation(captured):
    result = runner.invoke(
        app, ["--pod", "pod-1", "chat", "builder", "-m", "Task: build it"]
    )

    assert result.exit_code == 0, result.output
    assert captured["create"] == {"agent_name": "builder", "title": None}
    assert captured["send"] == ("new-conversation", "Task: build it")


def test_no_agent_named_also_ignores_the_ambient_conversation(captured):
    # Posting into the caller's own conversation only makes the caller answer
    # itself, so the env var never selects a chat's conversation.
    result = runner.invoke(app, ["--pod", "pod-1", "chat", "-m", "hello"])

    assert result.exit_code == 0, result.output
    assert captured["create"] == {"agent_name": None, "title": None}
    assert captured["send"] == ("new-conversation", "hello")


def test_explicit_conversation_for_the_named_agent_is_continued(captured):
    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "chat",
            "builder",
            "-m",
            "next step",
            "--conversation",
            "builder-conversation",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "create" not in captured
    assert captured["send"] == ("builder-conversation", "next step")


def test_explicit_conversation_of_another_agent_is_refused(captured):
    result = runner.invoke(
        app,
        ["--pod", "pod-1", "chat", "builder", "-m", "hi", "--conversation", AMBIENT],
    )

    assert result.exit_code == 1
    assert "belongs to a different agent" in result.output
    assert "send" not in captured
    assert "create" not in captured


def test_the_assistants_conversation_is_refused_to_a_named_agent(captured):
    # The send carries only the conversation, so the assistant would answer.
    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "chat",
            "builder",
            "-m",
            "hi",
            "--conversation",
            "assistant-conversation",
        ],
    )

    assert result.exit_code == 1
    assert "belongs to a different agent" in result.output
    assert "send" not in captured


def test_the_assistants_conversation_is_continued_when_the_assistant_is_named(captured):
    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "chat",
            "pod_default",
            "-m",
            "hi",
            "--conversation",
            "assistant-conversation",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["send"] == ("assistant-conversation", "hi")


def test_explicit_conversation_without_an_agent_is_used_unchecked(captured):
    result = runner.invoke(
        app, ["--pod", "pod-1", "chat", "-m", "hi", "--conversation", AMBIENT]
    )

    assert result.exit_code == 0, result.output
    assert "get" not in captured
    assert captured["send"] == (AMBIENT, "hi")


def test_agents_run_refuses_another_agents_conversation(captured):
    result = runner.invoke(
        app,
        ["--pod", "pod-1", "agents", "run", "builder", "hi", "--conversation", AMBIENT],
    )

    assert result.exit_code == 1
    assert "belongs to a different agent" in result.output
    assert "send" not in captured


def _state(*, runtime=None, default=None):
    config: dict[str, object] = {"_runtime": runtime or {}}
    if default:
        config["defaults"] = {"conversation_id": default}
    return SimpleNamespace(config=config)


@pytest.mark.parametrize(
    "explicit,runtime,default,agent,expected",
    [
        # The env var alone never selects a conversation, agent named or not.
        (None, None, None, "builder", None),
        (None, None, None, None, None),
        # --conversation and the global --conversation-id always count.
        ("c-flag", None, None, "builder", "c-flag"),
        (None, {"conversation": "c-global"}, None, "builder", "c-global"),
        # A saved `conversations select` default resumes only the default chat.
        (None, None, "c-saved", None, "c-saved"),
        (None, None, "c-saved", "builder", None),
    ],
)
def test_chat_conversation_resolution(
    monkeypatch, explicit, runtime, default, agent, expected
):
    monkeypatch.setenv("LEMMA_CONVERSATION_ID", AMBIENT)
    state = _state(runtime=runtime, default=default)

    assert chat_conversation(state, explicit, agent=agent) == expected
