"""A synthesized tool return never overwrites a real one.

Closing a run's open tool calls is what stops an abandoned approval card from
offering buttons forever. It runs at terminal, though, which is *after* a user
who answered the card already has their decision written — so without this the
answer they gave would be replaced by "the run ended before this was answered".
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid7

from app.modules.agent.domain.value_objects import MessageDraft
from app.modules.agent.services.run_message_writer import RunMessageWriter


class _Repository:
    def __init__(self, existing: object | None) -> None:
        self.existing = existing
        self.appended: list[MessageDraft] = []

    async def get_tool_return(self, *, conversation_id, tool_call_id):  # noqa: ARG002
        return self.existing

    async def append_message(self, *, conversation_id, agent_run_id, draft):  # noqa: ARG002
        self.appended.append(draft)
        return draft


def _writer(monkeypatch, repository: _Repository) -> RunMessageWriter:
    @asynccontextmanager
    async def uow_factory():
        yield object()

    monkeypatch.setattr(
        "app.modules.agent.services.run_message_writer.ConversationRepository",
        lambda _uow: repository,
    )
    return RunMessageWriter(uow_factory)


def _synthetic_return() -> MessageDraft:
    return MessageDraft.of_tool_return(
        tool_name="request_approval",
        tool_call_id="agent-host-permission:toolu_1",
        tool_result={"success": False, "error": "The agent run failed."},
        metadata={"synthetic_tool_return": True},
    )


async def test_a_decision_already_recorded_is_left_alone(monkeypatch) -> None:
    repository = _Repository(existing="the user's decision")
    writer = _writer(monkeypatch, repository)

    saved = await writer.persist(
        conversation_id=uuid7(),
        agent_run_id=uuid7(),
        data=_synthetic_return(),
    )

    assert saved == "the user's decision"
    assert repository.appended == []


async def test_a_call_nobody_answered_is_closed(monkeypatch) -> None:
    repository = _Repository(existing=None)
    writer = _writer(monkeypatch, repository)

    await writer.persist(
        conversation_id=uuid7(),
        agent_run_id=uuid7(),
        data=_synthetic_return(),
    )

    assert [draft.tool_call_id for draft in repository.appended] == [
        "agent-host-permission:toolu_1"
    ]


async def test_an_ordinary_tool_return_is_written_without_a_lookup(
    monkeypatch,
) -> None:
    """The check is scoped to synthesized returns: every other return is a fact
    the harness just produced, and paying a query for each one would slow the
    hot path to protect a case that cannot happen there."""
    repository = _Repository(existing="would be found if it looked")
    writer = _writer(monkeypatch, repository)

    await writer.persist(
        conversation_id=uuid7(),
        agent_run_id=uuid7(),
        data=MessageDraft.of_tool_return(
            tool_name="exec_command",
            tool_call_id="toolu_1",
            tool_result={"success": True},
        ),
    )

    assert len(repository.appended) == 1
