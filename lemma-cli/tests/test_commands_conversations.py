from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import conversations

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fake helpers
# ---------------------------------------------------------------------------


class FakeConversations:
    def list(self, *, agent_name=None, parent_id=None, type=None, limit=20):
        return {"items": [{"id": "conv-1", "title": "Test Chat", "status": "IDLE"}]}

    def get(self, conversation_id):
        return {"id": conversation_id, "title": "Test Chat", "status": "IDLE"}

    def messages(self, conversation_id, *, limit=100):
        return {
            "items": [
                {"id": "msg-1", "role": "user", "content": "hello"},
                {"id": "msg-2", "role": "assistant", "content": "hi there"},
            ]
        }

    def stop(self, conversation_id):
        return {"id": conversation_id, "status": "STOPPING"}


def _make_fake_pod(fake_convs):
    return SimpleNamespace(conversations=fake_convs, pod_id="pod-1")


def _make_fake_run(fake_convs):
    def fake_run_with_client(ctx, fn):
        client = SimpleNamespace(
            pod=lambda pod_id: _make_fake_pod(fake_convs),
            conversations=fake_convs,
        )
        state = SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty")
        return fn(client, state)

    return fake_run_with_client


# ---------------------------------------------------------------------------
# 1. conversations list
# ---------------------------------------------------------------------------


def test_conversations_list_dispatches_api(monkeypatch):
    fake_convs = FakeConversations()
    monkeypatch.setattr(conversations, "run_with_client", _make_fake_run(fake_convs))

    result = runner.invoke(app, ["--pod", "pod-1", "conversations", "list"])

    assert result.exit_code == 0, result.stdout
    assert "conv-1" in result.stdout


# ---------------------------------------------------------------------------
# 2. conversations list --json
# ---------------------------------------------------------------------------


def test_conversations_list_json_output(monkeypatch):
    fake_convs = FakeConversations()
    monkeypatch.setattr(conversations, "run_with_client", _make_fake_run(fake_convs))

    result = runner.invoke(app, ["--json", "--pod", "pod-1", "conversations", "list"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "items" in payload
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == "conv-1"


# ---------------------------------------------------------------------------
# 3. conversations get
# ---------------------------------------------------------------------------


def test_conversations_get_dispatches_api(monkeypatch):
    captured = {}

    class CapturingConversations(FakeConversations):
        def get(self, conversation_id):
            captured["id"] = conversation_id
            return {"id": conversation_id, "title": "Test Chat"}

    fake_convs = CapturingConversations()
    monkeypatch.setattr(conversations, "run_with_client", _make_fake_run(fake_convs))

    result = runner.invoke(app, ["conversations", "get", "conv-1", "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert captured.get("id") == "conv-1"


# ---------------------------------------------------------------------------
# 4. conversations messages (transcript view)
# ---------------------------------------------------------------------------


def test_conversations_messages_dispatches_api(monkeypatch):
    captured = {}

    class CapturingConversations(FakeConversations):
        def messages(self, conversation_id, *, limit=100):
            captured["id"] = conversation_id
            captured["limit"] = limit
            return {
                "items": [
                    {"id": "msg-1", "role": "user", "content": "hello"},
                ]
            }

    fake_convs = CapturingConversations()
    monkeypatch.setattr(conversations, "run_with_client", _make_fake_run(fake_convs))

    result = runner.invoke(
        app, ["conversations", "messages", "conv-1", "--pod", "pod-1"]
    )

    assert result.exit_code == 0, result.stdout
    assert captured.get("id") == "conv-1"


# ---------------------------------------------------------------------------
# 5. conversations stop
# ---------------------------------------------------------------------------


def test_conversations_stop_dispatches_api(monkeypatch):
    captured = {}

    class CapturingConversations(FakeConversations):
        def stop(self, conversation_id):
            captured["id"] = conversation_id
            return {"id": conversation_id, "status": "STOPPING"}

    fake_convs = CapturingConversations()
    monkeypatch.setattr(conversations, "run_with_client", _make_fake_run(fake_convs))

    result = runner.invoke(app, ["conversations", "stop", "conv-1", "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert captured.get("id") == "conv-1"
