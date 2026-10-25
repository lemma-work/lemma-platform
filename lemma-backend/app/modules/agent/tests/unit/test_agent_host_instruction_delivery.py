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
from datetime import datetime, timezone

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
        self.locked_reads = 0

    async def get_conversation_metadata_key(self, *_args, **kwargs) -> object:
        if kwargs.get("for_update"):
            self.locked_reads += 1
        return self.stored

    async def set_conversation_metadata_key(
        self, _conversation_id, _key, value
    ) -> None:
        self.stored = value


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> _Repository:
    instance = _Repository()
    monkeypatch.setattr(memory, "Conversations", lambda _uow: instance)
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


def _checkpoint(
    run_id,
    session_id: str,
    state: AgentHostRunState = AgentHostRunState.RUNNING,
) -> AgentHostRunCheckpoint:
    return AgentHostRunCheckpoint(
        run_id=run_id,
        lease_epoch=1,
        state=state,
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
    other_run, dispatched_run = uuid7(), uuid7()
    created_at = datetime.now(timezone.utc)

    class _Result:
        def one_or_none(self):
            return (conversation_id, harness_id, created_at, harness_id)

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


@pytest.mark.anyio
async def test_how_far_a_prompt_ran_is_recorded_only_once_it_landed(
    repository: _Repository,
) -> None:
    """``seen_through`` rides the same promise as the instructions: kept
    pending at dispatch, promoted only by a checkpoint that proves the prompt
    landed, and never taken from another run's."""
    conversation_id, harness_id = uuid4(), uuid4()
    other_run, dispatched_run = uuid7(), uuid7()
    uow = TestASessionIdIsNotProofThePromptLanded._uow(conversation_id, harness_id)

    await memory.record_pending_instructions(
        uow,
        conversation_id=conversation_id,
        run_id=dispatched_run,
        digest="abc",
        seen_through=41,
    )
    await memory.remember_provider_session(
        uow, _checkpoint(dispatched_run, "sess-1", AgentHostRunState.FAILED)
    )
    assert "prompted_through" not in repository.stored

    await memory.remember_provider_session(uow, _checkpoint(other_run, "sess-1"))
    assert "prompted_through" not in repository.stored
    assert repository.stored["pending_prompt"]["seen_through"] == 41

    await memory.remember_provider_session(uow, _checkpoint(dispatched_run, "sess-1"))
    assert repository.stored["prompted_through"] == 41
    assert repository.stored["prompted_run_id"] == str(dispatched_run)
    assert "pending_prompt" not in repository.stored

    # Every later heartbeat of the same run lands again, with nothing pending:
    # the mark it promoted stays.
    for state in (AgentHostRunState.RUNNING, AgentHostRunState.SUCCEEDED):
        await memory.remember_provider_session(
            uow, _checkpoint(dispatched_run, "sess-1", state)
        )
        assert repository.stored["prompted_through"] == 41


class TestASessionIdIsNotProofThePromptLanded:
    """The host writes `provider_session_id` in `before_prompt` -- *before*
    `session/prompt` is sent -- and the journal then attaches it to every
    checkpoint, terminal ones included.

    So a prompt that failed (a context ceiling, an adapter fault) still reported
    FAILED carrying a session id, and that used to promote the digest. Every
    later turn then dispatched NEW_SESSION_ONLY, and nothing un-promotes a
    digest: the agent ran without its instructions for the rest of the
    conversation.

    The asymmetry decides the gate. Failing to promote costs one re-send of the
    instructions; promoting wrongly costs every future turn.
    """

    @staticmethod
    def _uow(conversation_id, harness_id):
        created_at = datetime.now(timezone.utc)

        class _Result:
            def one_or_none(self):
                return (conversation_id, harness_id, created_at, harness_id)

        class _Session:
            async def execute(self, *_args, **_kwargs):
                return _Result()

        return type("_Uow", (), {"session": _Session()})()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "state",
        [
            AgentHostRunState.FAILED,
            AgentHostRunState.CANCELLED,
            AgentHostRunState.DISPATCH_UNKNOWN,
            AgentHostRunState.RECOVERING,
            AgentHostRunState.ACCEPTED,
            AgentHostRunState.DISPATCHING,
        ],
    )
    async def test_a_state_that_may_precede_the_prompt_does_not_promote(
        self, repository: _Repository, state: AgentHostRunState
    ) -> None:
        conversation_id, harness_id = uuid4(), uuid4()
        run_id = uuid7()
        uow = self._uow(conversation_id, harness_id)

        await memory.record_pending_instructions(
            uow, conversation_id=conversation_id, run_id=run_id, digest="abc"
        )
        await memory.remember_provider_session(
            uow, _checkpoint(run_id, "sess-1", state)
        )

        assert "instructions_digest" not in repository.stored, (
            f"{state.value} promoted the digest, so the next turn will skip "
            "instructions that may never have reached the session"
        )
        assert repository.stored["pending_instructions"]["digest"] == "abc", (
            "the promise was dropped, so the run that made it can never record it"
        )

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "state",
        [
            AgentHostRunState.RUNNING,
            AgentHostRunState.WAITING_INPUT,
            AgentHostRunState.SUCCEEDED,
        ],
    )
    async def test_a_state_the_host_cannot_reach_without_prompting_promotes(
        self, repository: _Repository, state: AgentHostRunState
    ) -> None:
        conversation_id, harness_id = uuid4(), uuid4()
        run_id = uuid7()
        uow = self._uow(conversation_id, harness_id)

        await memory.record_pending_instructions(
            uow, conversation_id=conversation_id, run_id=run_id, digest="abc"
        )
        await memory.remember_provider_session(
            uow, _checkpoint(run_id, "sess-1", state)
        )

        assert repository.stored["instructions_digest"] == "abc"

    @pytest.mark.anyio
    async def test_a_failed_turn_still_delivers_on_the_next_one(
        self, repository: _Repository
    ) -> None:
        """The end-to-end shape: a failure must not cost the user's edit."""
        conversation_id, harness_id = uuid4(), uuid4()
        first, second = uuid7(), uuid7()
        uow = self._uow(conversation_id, harness_id)

        await memory.record_pending_instructions(
            uow, conversation_id=conversation_id, run_id=first, digest="abc"
        )
        await memory.remember_provider_session(
            uow, _checkpoint(first, "sess-1", AgentHostRunState.FAILED)
        )
        assert not await memory.instructions_already_delivered(
            uow,
            conversation_id=conversation_id,
            harness_id=harness_id,
            digest="abc",
        ), "the next turn would skip instructions the session never received"

        await memory.record_pending_instructions(
            uow, conversation_id=conversation_id, run_id=second, digest="abc"
        )
        await memory.remember_provider_session(uow, _checkpoint(second, "sess-1"))
        assert await memory.instructions_already_delivered(
            uow,
            conversation_id=conversation_id,
            harness_id=harness_id,
            digest="abc",
        )


@pytest.mark.anyio
async def test_a_heartbeat_that_changes_nothing_takes_no_lock(
    repository: _Repository,
) -> None:
    """The host resends its session id on every heartbeat. Locking the
    conversation's metadata to learn nothing had changed contended with message
    appends on exactly the busiest conversations."""
    conversation_id, harness_id = uuid4(), uuid4()
    run_id = uuid7()
    uow = TestASessionIdIsNotProofThePromptLanded._uow(conversation_id, harness_id)

    assert await memory.remember_provider_session(uow, _checkpoint(run_id, "sess-1"))
    locked_by_the_first = repository.locked_reads

    for _ in range(3):
        assert not await memory.remember_provider_session(
            uow, _checkpoint(run_id, "sess-1")
        )

    assert locked_by_the_first == 1
    assert repository.locked_reads == locked_by_the_first


class TestHowFarTheSessionHasRead:
    """A resumed turn sends what was written in no run since the session's
    last turn (``turn_messages``), so Lemma has to know which turn that was:
    the newest run whose prompt landed, which the run the binding names --
    the newest to report the session at all -- need not be."""

    @staticmethod
    async def _last_run(uow, conversation_id, harness_id):
        session = await memory.resumed_session(
            uow,
            conversation_id=conversation_id,
            harness_id=harness_id,
            capabilities={"load_session": True},
        )
        assert session is not None
        return session.last_run_id

    @pytest.mark.anyio
    async def test_a_run_counts_once_its_prompt_has_landed(
        self, repository: _Repository
    ) -> None:
        conversation_id, harness_id = uuid4(), uuid4()
        first, second = uuid7(), uuid7()
        uow = TestASessionIdIsNotProofThePromptLanded._uow(conversation_id, harness_id)

        await memory.remember_provider_session(
            uow, _checkpoint(first, "sess-1", AgentHostRunState.DISPATCHING)
        )
        assert await self._last_run(uow, conversation_id, harness_id) is None

        await memory.remember_provider_session(uow, _checkpoint(first, "sess-1"))
        assert await self._last_run(uow, conversation_id, harness_id) == first

        # Failed before its prompt was sent: the session holds the first
        # run's turn and nothing of this one's, whichever run it names.
        for state in (AgentHostRunState.DISPATCHING, AgentHostRunState.FAILED):
            await memory.remember_provider_session(
                uow, _checkpoint(second, "sess-1", state)
            )
        assert repository.stored["run_id"] == str(second)
        assert await self._last_run(uow, conversation_id, harness_id) == first

    @pytest.mark.anyio
    async def test_a_new_session_has_read_nothing(
        self, repository: _Repository
    ) -> None:
        conversation_id, harness_id = uuid4(), uuid4()
        first, second = uuid7(), uuid7()
        uow = TestASessionIdIsNotProofThePromptLanded._uow(conversation_id, harness_id)

        await memory.remember_provider_session(uow, _checkpoint(first, "sess-1"))
        await memory.remember_provider_session(
            uow, _checkpoint(second, "sess-2", AgentHostRunState.DISPATCHING)
        )

        assert await self._last_run(uow, conversation_id, harness_id) is None

    @pytest.mark.anyio
    async def test_a_binding_from_before_this_was_kept_names_no_run(
        self, repository: _Repository
    ) -> None:
        """So such a session is sent all the history carries in no run, none
        of which it was ever sent."""
        harness_id = uuid4()
        repository.stored = {
            "harness_id": str(harness_id),
            "session_id": "sess-1",
            "run_id": str(uuid7()),
        }

        assert await self._last_run(object(), uuid4(), harness_id) is None
