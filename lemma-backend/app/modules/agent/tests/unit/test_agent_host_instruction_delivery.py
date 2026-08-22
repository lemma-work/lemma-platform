"""Deciding whether a turn still has to carry Lemma's instructions.

A conversation is one provider session and a session keeps its own history, so
instructions delivered when it opened are still there on every later turn.
Re-sending them each time put another copy of a multi-kilobyte block into the
provider's transcript per message, which the model then re-read in full on
every turn after that.

The decision is split deliberately. Lemma answers "has this session already
been told exactly this text", which only it can know because a user can edit an
agent mid-conversation. The host answers "is this session actually new", which
only it can know because a provider is free to forget a session and turn an
expected resume into a fresh one. Neither side can answer both, and a design
where either decided alone has a case that runs the agent with no instructions
at all.
"""

from __future__ import annotations

from uuid import uuid4, uuid7

import pytest

from app.modules.agent.domain.agent_host import (
    NEW_SESSION_ONLY,
    AgentHostRunCheckpoint,
    AgentHostRunSpec,
    AgentHostRunState,
)
from app.modules.agent.infrastructure.agent_host import session_memory as memory


def test_the_digest_moves_when_the_instructions_do() -> None:
    """Taken over the text that lands on the spec, so any edit a user can make
    — agent instructions, conversation instructions, the granted-resource
    brief — shows up as a different fingerprint."""
    base = memory.instructions_digest("# Instructions\nBe exact.")

    assert base == memory.instructions_digest("# Instructions\nBe exact.")
    assert base != memory.instructions_digest("# Instructions\nBe brief.")
    assert base != memory.instructions_digest("")


class _Repository:
    """The two conversation-metadata calls this module makes, in memory."""

    def __init__(self, stored: object = None) -> None:
        self.stored = stored

    async def get_conversation_metadata_key(self, *_args, **_kwargs) -> object:
        return self.stored

    async def set_conversation_metadata_key(
        self, _conversation_id, _key, value
    ) -> None:
        self.stored = value


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> _Repository:
    instance = _Repository()
    monkeypatch.setattr(memory, "ConversationRepository", lambda _uow: instance)
    return instance


@pytest.mark.anyio
async def test_only_the_same_text_on_the_same_harness_counts_as_delivered(
    repository: _Repository,
) -> None:
    harness_id = uuid4()
    repository.stored = {
        "harness_id": str(harness_id),
        "session_id": "sess-1",
        "instructions_digest": "abc",
    }

    async def delivered(*, digest: str, harness: object = harness_id) -> bool:
        return await memory.instructions_already_delivered(
            object(),
            conversation_id=uuid4(),
            harness_id=harness,
            digest=digest,
        )

    assert await delivered(digest="abc")
    # Every uncertain case resolves toward sending them again: the failure this
    # guards against is an agent quietly running without its instructions.
    assert not await delivered(digest="changed")
    assert not await delivered(digest="abc", harness=uuid4())

    repository.stored = {"harness_id": str(harness_id), "session_id": "sess-1"}
    assert not await delivered(digest="abc")

    repository.stored = None
    assert not await delivered(digest="abc")


def test_the_host_is_told_to_send_them_unless_lemma_says_otherwise() -> None:
    """The wire default is the safe one. An older Lemma does not set the field
    at all, so a host reading a spec it did not expect keeps today's
    behaviour."""
    spec = AgentHostRunSpec(
        agent_run_id=uuid7(),
        conversation_id=uuid4(),
        harness_id=uuid4(),
        profile_revision="rev",
        system_prompt="Be exact.",
        prompt=[{"type": "text", "text": "Hello"}],
        run_deadline=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )

    assert spec.system_prompt_delivery is None
    assert (
        spec.model_copy(
            update={"system_prompt_delivery": NEW_SESSION_ONLY}
        ).system_prompt_delivery
        == NEW_SESSION_ONLY
    )


def _checkpoint(run_id, session_id: str) -> AgentHostRunCheckpoint:
    return AgentHostRunCheckpoint(
        run_id=run_id,
        lease_epoch=1,
        state=AgentHostRunState.RUNNING,
        detail={"provider_session_id": session_id},
    )


@pytest.mark.anyio
async def test_instructions_count_as_delivered_only_once_the_host_prompted(
    monkeypatch: pytest.MonkeyPatch, repository: _Repository
) -> None:
    """Record delivery, not intent.

    A run that dies before it prompts — host offline, expired command, an
    adapter that would not start — delivered nothing. Marking its instructions
    delivered at dispatch would make every later turn skip them, silently
    losing a user's edit for the rest of the conversation.
    """
    conversation_id, harness_id = uuid4(), uuid4()
    dispatched_run, other_run = uuid7(), uuid7()

    class _Result:
        def one_or_none(self):
            return (conversation_id, harness_id)

    class _Session:
        async def execute(self, *_args, **_kwargs):
            return _Result()

    uow = type("_Uow", (), {"session": _Session()})()

    await memory.record_pending_instructions(
        uow, conversation_id=conversation_id, run_id=dispatched_run, digest="abc"
    )
    assert "instructions_digest" not in repository.stored

    # A checkpoint from some other run proves nothing about this promise.
    await memory.remember_provider_session(uow, _checkpoint(other_run, "sess-1"))
    assert "instructions_digest" not in repository.stored

    await memory.remember_provider_session(uow, _checkpoint(dispatched_run, "sess-1"))
    assert repository.stored["instructions_digest"] == "abc"
