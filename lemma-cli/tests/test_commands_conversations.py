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


# ---------------------------------------------------------------------------
# 6. conversations approve
# ---------------------------------------------------------------------------


class ApprovingConversations(FakeConversations):
    """Two pending approvals, and a record of what was resolved."""

    def __init__(self, *, failing: set[str] | None = None) -> None:
        self.resolved: list[tuple[str, str]] = []
        self.failing = failing or set()

    def approvals(self, conversation_id):
        return {
            "items": [
                {"id": "ap-1", "tool_name": "send_email", "tool_args": {"to": "x@y.z"}},
                {"id": "ap-2", "tool_name": "write_file"},
            ]
        }

    def resolve_approval(self, conversation_id, approval_id, request):
        if approval_id in self.failing:
            from lemma_sdk.errors import LemmaAPIError

            raise LemmaAPIError(status_code=409, message="already resolved")
        self.resolved.append((approval_id, request.decision))
        return {"id": approval_id}


def test_approve_all_refuses_without_yes_and_resolves_nothing(monkeypatch):
    """A bare `lemma conversation approve` authorises every gated call queued on
    the conversation — mail, file writes, connector operations. CONVENTIONS.md
    requires confirm_destructive on any irreversible command, and this one had
    neither a confirmation nor a preview."""
    fake_convs = ApprovingConversations()
    monkeypatch.setattr(conversations, "run_with_client", _make_fake_run(fake_convs))

    result = runner.invoke(app, ["conversation", "approve", "-c", "conv-1"])

    assert result.exit_code == 1, result.output
    assert fake_convs.resolved == []
    flat = " ".join(result.stderr.split())
    assert "--yes" in flat
    # The preview names what would have been approved.
    assert "send_email" in flat
    assert "write_file" in flat


def test_approve_all_with_yes_resolves_every_pending(monkeypatch):
    fake_convs = ApprovingConversations()
    monkeypatch.setattr(conversations, "run_with_client", _make_fake_run(fake_convs))

    result = runner.invoke(
        app, ["--json", "conversation", "approve", "-c", "conv-1", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert fake_convs.resolved == [
        ("ap-1", "APPROVE_ONCE"),
        ("ap-2", "APPROVE_ONCE"),
    ]
    assert json.loads(result.stdout)["resolved"] == ["ap-1", "ap-2"]


def test_approve_one_by_id_needs_no_confirmation(monkeypatch):
    fake_convs = ApprovingConversations()
    monkeypatch.setattr(conversations, "run_with_client", _make_fake_run(fake_convs))

    result = runner.invoke(
        app, ["--json", "conversation", "approve", "ap-1", "-c", "conv-1"]
    )

    assert result.exit_code == 0, result.output
    assert fake_convs.resolved == [("ap-1", "APPROVE_ONCE")]


def test_approve_reports_which_approvals_went_through_when_one_fails(monkeypatch):
    """Without per-approval handling the command failed on the second of two and
    never said the first had already been authorised."""
    fake_convs = ApprovingConversations(failing={"ap-2"})
    monkeypatch.setattr(conversations, "run_with_client", _make_fake_run(fake_convs))

    result = runner.invoke(
        app, ["--json", "conversation", "approve", "-c", "conv-1", "--yes"]
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["resolved"] == ["ap-1"]
    assert payload["failed"][0]["id"] == "ap-2"


def test_approve_with_nothing_pending_is_a_no_op(monkeypatch):
    class Empty(FakeConversations):
        def approvals(self, conversation_id):
            return {"items": []}

    monkeypatch.setattr(conversations, "run_with_client", _make_fake_run(Empty()))

    result = runner.invoke(app, ["--json", "conversation", "approve", "-c", "conv-1"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["resolved"] == []
