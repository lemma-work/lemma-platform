"""One pod asking another: who may ask as their person, how far, and what answers.

The rules in ``domain/pod_asks.py`` are all about the answer's audience: it
lands in the asking conversation, so that conversation has to be one only the
person reads, started by them, with the assistant acting as them.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.pod_asks import (
    ANSWER_SOURCE,
    ASK_KEY,
    HANDED_BACK_SOURCES,
    MAX_ASK_DEPTH,
    AskChain,
    AskingRun,
    AskMode,
    AskRefused,
    PodAsk,
    answer_from,
    ask_of,
    chain_for_person,
    linked_asker,
    plan_ask,
)
from app.modules.agent.domain.outsiders import answers_outsiders
from app.modules.agent.domain.server_metadata import (
    client_metadata,
    with_server_keys_kept,
)
from app.modules.agent.domain.value_objects import (
    ConversationType,
    MessageKind,
    MessageRole,
)

pytestmark = pytest.mark.unit

PERSON = uuid4()
HERE = uuid4()
THERE = uuid4()


def _conversation(
    *,
    user_id: UUID = PERSON,
    type: ConversationType = ConversationType.CHAT,
    metadata: dict[str, object] | None = None,
) -> Conversation:
    return Conversation(user_id=user_id, pod_id=HERE, type=type, metadata=metadata)


def _run(
    conversation: Conversation | None = None,
    *,
    default_agent: bool = True,
    outsider: bool = False,
    surface_kind: str | None = None,
    turn_source: str | None = None,
) -> AskingRun:
    return AskingRun(
        user_id=PERSON,
        pod_id=HERE,
        conversation=conversation or _conversation(),
        is_pod_default_agent=default_agent,
        answers_outsider=outsider,
        surface_conversation_kind=surface_kind,
        latest_turn_source=turn_source,
    )


def _ask(*, for_user_id: UUID = PERSON, chain: AskChain | None = None) -> PodAsk:
    return PodAsk(
        from_pod_id=uuid4(),
        from_pod_name="Support",
        from_conversation_id=uuid4(),
        for_user_id=for_user_id,
        mode=AskMode.AS_PERSON,
        chain=chain or AskChain.start(uuid4()).onward_to(HERE),
    )


class TestTheChain:
    def test_each_hop_adds_the_pod_and_one_to_the_depth(self):
        chain = AskChain.start(HERE).onward_to(THERE)
        assert chain.depth == 1
        assert chain.pod_ids == (HERE, THERE)

    def test_a_pod_already_on_the_chain_is_refused(self):
        chain = AskChain.start(HERE).onward_to(THERE)
        with pytest.raises(AskRefused, match="already part of this request"):
            chain.onward_to(HERE)

    def test_the_chain_stops_at_its_depth(self):
        chain = AskChain.start(uuid4())
        for _ in range(MAX_ASK_DEPTH):
            chain = chain.onward_to(uuid4())
        with pytest.raises(AskRefused, match="as far as a request may go"):
            chain.onward_to(uuid4())

    def test_it_survives_the_trip_through_metadata(self):
        chain = AskChain.start(HERE).onward_to(THERE)
        assert AskChain.from_metadata(chain.to_metadata()) == chain


class TestTheAskOnTheConversation:
    def test_it_survives_the_trip_through_metadata(self):
        ask = _ask()
        conversation = _conversation(metadata={ASK_KEY: ask.to_metadata()})
        assert ask_of(conversation) == ask

    @pytest.mark.parametrize(
        "raw",
        [None, "an ask", {"from_pod_id": "not a uuid"}, {"mode": "AS_PERSON"}],
    )
    def test_anything_that_does_not_read_as_an_ask_is_none(self, raw):
        assert ask_of(_conversation(metadata={ASK_KEY: raw})) is None

    def test_a_client_can_neither_change_nor_drop_it(self):
        stored = {ASK_KEY: _ask().to_metadata(), "pinned": True}
        forged = {ASK_KEY: _ask(for_user_id=uuid4()).to_metadata(), "pinned": False}
        assert with_server_keys_kept(stored, forged) == {
            ASK_KEY: stored[ASK_KEY],
            "pinned": False,
        }
        assert with_server_keys_kept(stored, None) == {ASK_KEY: stored[ASK_KEY]}

    def test_a_client_cannot_open_a_conversation_claiming_one(self):
        assert client_metadata({ASK_KEY: {"anything": 1}, "cwd": "/me"}) == {
            "cwd": "/me"
        }


class TestWhoMayAskAsTheirPerson:
    def test_the_assistant_in_the_persons_own_chat_may(self):
        chain = chain_for_person(_run(), to_pod_id=THERE)
        assert chain.pod_ids == (HERE, THERE)

    def test_a_direct_message_is_the_persons_alone(self):
        chain_for_person(_run(surface_kind="dm"), to_pod_id=THERE)

    def test_not_this_pod(self):
        with pytest.raises(AskRefused, match="That is this pod"):
            chain_for_person(_run(), to_pod_id=HERE)

    def test_not_a_named_agent(self):
        with pytest.raises(AskRefused, match="named agent"):
            chain_for_person(_run(default_agent=False), to_pod_id=THERE)

    def test_not_a_conversation_answering_strangers(self):
        with pytest.raises(AskRefused, match="outside the pod"):
            chain_for_person(_run(outsider=True), to_pod_id=THERE)

    @pytest.mark.parametrize("kind", ["CHANNEL", "EMAIL"])
    def test_not_a_thread_other_people_read(self, kind):
        with pytest.raises(AskRefused, match="Other people read this thread"):
            chain_for_person(_run(surface_kind=kind), to_pod_id=THERE)

    def test_not_a_conversation_nobody_started_by_hand(self):
        task = _conversation(type=ConversationType.TASK)
        with pytest.raises(AskRefused, match="Nobody started this"):
            chain_for_person(_run(task), to_pod_id=THERE)
        scheduled = _conversation(metadata={"started_by": "SCHEDULE"})
        with pytest.raises(AskRefused, match="Nobody started this"):
            chain_for_person(_run(scheduled), to_pod_id=THERE)

    def test_not_a_sub_agent(self):
        child = _conversation(metadata={"is_sub_agent": True})
        with pytest.raises(AskRefused, match="sub-agent"):
            chain_for_person(_run(child), to_pod_id=THERE)

    def test_not_somebody_elses_conversation(self):
        theirs = _conversation(user_id=uuid4())
        with pytest.raises(AskRefused, match="belongs to"):
            chain_for_person(_run(theirs), to_pod_id=THERE)

    @pytest.mark.parametrize("source", sorted(HANDED_BACK_SOURCES))
    def test_not_from_a_turn_an_answer_started(self, source):
        """Otherwise two pods could hand work back and forth with nobody asking."""
        with pytest.raises(AskRefused, match="not with something the person wrote"):
            chain_for_person(_run(turn_source=source), to_pod_id=THERE)

    @pytest.mark.parametrize("source", [None, "agent_surfaces", "queued_messages"])
    def test_a_turn_the_person_started_may(self, source):
        chain_for_person(_run(turn_source=source), to_pod_id=THERE)

    def test_the_handed_back_sources_are_the_ones_the_platform_writes(self):
        from app.modules.agent.services.message_reply_service import REPLY_SOURCE

        assert HANDED_BACK_SOURCES == {ANSWER_SOURCE, REPLY_SOURCE}

    def test_answering_an_ask_for_the_same_person_carries_the_chain_on(self):
        incoming = _ask()
        answering = _conversation(metadata={ASK_KEY: incoming.to_metadata()})
        chain = chain_for_person(_run(answering), to_pod_id=THERE)
        assert chain.chain_id == incoming.chain.chain_id
        assert chain.depth == incoming.chain.depth + 1

    def test_answering_an_ask_cannot_ask_back_the_pod_that_asked(self):
        asker = uuid4()
        incoming = _ask(chain=AskChain.start(asker).onward_to(HERE))
        answering = _conversation(metadata={ASK_KEY: incoming.to_metadata()})
        with pytest.raises(AskRefused, match="already part of this request"):
            chain_for_person(_run(answering), to_pod_id=asker)

    def test_answering_an_ask_for_someone_else_cannot_ask_on(self):
        incoming = _ask(for_user_id=uuid4())
        answering = _conversation(metadata={ASK_KEY: incoming.to_metadata()})
        with pytest.raises(AskRefused, match="for someone else"):
            chain_for_person(_run(answering), to_pod_id=THERE)


def _message(
    sequence: int,
    run_id: UUID,
    *,
    role: MessageRole = MessageRole.ASSISTANT,
    kind: MessageKind = MessageKind.TEXT,
    text: str | None = None,
) -> Message:
    return Message.create(
        conversation_id=uuid4(),
        sequence=sequence,
        agent_run_id=run_id,
        role=role,
        kind=kind,
        text=text,
        tool_name="pod_query" if kind is not MessageKind.TEXT else None,
        tool_call_id="call-1" if kind is not MessageKind.TEXT else None,
    )


class TestTheAnswer:
    def test_it_is_what_the_run_wrote_after_its_last_tool_call(self):
        run = uuid4()
        messages = [
            _message(1, run, role=MessageRole.USER, text="Why did invoice 42 fail?"),
            _message(2, run, text="Let me look at the ledger."),
            _message(3, run, kind=MessageKind.TOOL_CALL),
            _message(4, run, role=MessageRole.TOOL, kind=MessageKind.TOOL_RETURN),
            _message(5, run, text="The card expired on the 3rd."),
            _message(6, run, text="A new one was requested."),
        ]
        assert answer_from(messages, run_id=run) == (
            "The card expired on the 3rd.\n\nA new one was requested."
        )

    def test_a_run_that_called_nothing_answers_with_everything_it_wrote(self):
        run = uuid4()
        assert answer_from([_message(1, run, text="Yes.")], run_id=run) == "Yes."

    def test_another_runs_words_are_not_this_ones_answer(self):
        earlier, this = uuid4(), uuid4()
        messages = [_message(1, earlier, text="Old answer.")]
        assert answer_from(messages, run_id=this) is None


def _link_ask(*, from_pod: UUID) -> PodAsk:
    return PodAsk(
        from_pod_id=from_pod,
        from_pod_name="Support",
        from_conversation_id=uuid4(),
        for_user_id=None,
        mode=AskMode.LINK,
        chain=AskChain.start(from_pod).onward_to(HERE),
    )


class TestHowItAsks:
    def test_as_the_person_when_they_are_here_and_in_both(self):
        mode, _ = plan_ask(_run(), to_pod_id=THERE, through_you=True, connected=True)
        assert mode is AskMode.AS_PERSON

    def test_over_the_link_when_nobody_started_this(self):
        task = _conversation(type=ConversationType.TASK)
        mode, chain = plan_ask(
            _run(task), to_pod_id=THERE, through_you=True, connected=True
        )
        assert mode is AskMode.LINK
        assert chain.pod_ids == (HERE, THERE)

    def test_over_the_link_when_other_people_read_the_thread(self):
        mode, _ = plan_ask(
            _run(surface_kind="CHANNEL"),
            to_pod_id=THERE,
            through_you=True,
            connected=True,
        )
        assert mode is AskMode.LINK

    def test_not_at_all_when_neither_reaches_it(self):
        task = _conversation(type=ConversationType.TASK)
        with pytest.raises(AskRefused, match="Nobody started this"):
            plan_ask(_run(task), to_pod_id=THERE, through_you=True, connected=False)
        with pytest.raises(AskRefused, match="isn't connected"):
            plan_ask(_run(), to_pod_id=THERE, through_you=False, connected=False)

    def test_a_link_still_refuses_strangers_and_answers_coming_back(self):
        with pytest.raises(AskRefused, match="outside the pod"):
            plan_ask(
                _run(outsider=True), to_pod_id=THERE, through_you=False, connected=True
            )
        with pytest.raises(AskRefused, match="answer coming back"):
            plan_ask(
                _run(turn_source=ANSWER_SOURCE),
                to_pod_id=THERE,
                through_you=False,
                connected=True,
            )

    def test_a_conversation_answering_a_link_cannot_ask_on(self):
        answering = _conversation(
            metadata={ASK_KEY: _link_ask(from_pod=uuid4()).to_metadata()}
        )
        with pytest.raises(AskRefused):
            plan_ask(
                _run(answering), to_pod_id=THERE, through_you=False, connected=True
            )


class TestALinkConversation:
    def test_it_answers_from_outside_the_pod_as_the_asking_pod(self):
        asker = uuid4()
        conversation = _conversation(
            metadata={ASK_KEY: _link_ask(from_pod=asker).to_metadata()}
        )
        assert linked_asker(conversation) == asker
        assert answers_outsiders(conversation)

    def test_an_ask_as_a_person_is_not_one(self):
        conversation = _conversation(metadata={ASK_KEY: _ask().to_metadata()})
        assert linked_asker(conversation) is None
        assert not answers_outsiders(conversation)

    def test_asking_as_a_person_needs_a_person(self):
        raw = {**_ask().to_metadata(), "for_user_id": None}
        assert ask_of(_conversation(metadata={ASK_KEY: raw})) is None


def test_an_answer_awaited_inline_is_left_to_the_asker_until_then():
    now = datetime.now(timezone.utc)
    ask = replace(_ask(), inline_until=now + timedelta(seconds=20))
    assert ask.awaited_inline(now)
    assert not ask.awaited_inline(now + timedelta(seconds=21))
    assert PodAsk.from_metadata(ask.to_metadata()) == ask
