"""Which runs may use their person's own machine: ``contracts/run_audience``.

Whom the conversation answers decides it. Every run a member owns reaches
their machine -- a chat, a schedule's, a workflow's, a channel's, a call's, a
sub-agent's -- and a run answering a contact or another pod never does.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.contracts.run_audience import answers_member
from app.modules.agent.domain.calls import CallMark, CallRole
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pod_ask_record import AskChain, AskMode, PodAsk
from app.modules.agent.domain.value_objects import ConversationType

pytestmark = pytest.mark.unit

PERSON = uuid4()


def _conversation(**fields) -> Conversation:
    return Conversation(user_id=PERSON, pod_id=uuid4(), **fields)


def _ask(mode: AskMode) -> PodAsk:
    asking_pod = uuid4()
    return PodAsk(
        from_pod_id=asking_pod,
        from_pod_name="Other",
        from_conversation_id=uuid4(),
        for_user_id=None,
        mode=mode,
        chain=AskChain.start(asking_pod),
    )


@pytest.mark.parametrize(
    "conversation",
    [
        _conversation(),
        # A schedule's or a workflow's run is a task conversation of its own.
        _conversation(type=ConversationType.TASK),
        # A channel message: a Slack or WhatsApp group the person's run answers.
        _conversation(channel_id=uuid4()),
        # A sub-agent's conversation, spawned by the person's run.
        _conversation(parent_run_id=uuid4()),
        # A voice call.
        _conversation(call=CallMark(call_id=uuid4(), role=CallRole.COORDINATOR)),
        # Asked by the same person from another pod they are a member of.
        _conversation(ask=_ask(AskMode.AS_PERSON)),
    ],
    ids=["chat", "schedule", "channel", "subagent", "call", "ask-as-person"],
)
def test_every_run_a_member_owns_answers_a_member(conversation) -> None:
    assert answers_member(conversation)


def test_a_contacts_conversation_answers_an_outsider() -> None:
    assert not answers_member(_conversation(contact_id=uuid4()))


def test_another_pod_asking_over_a_link_is_an_outsider() -> None:
    assert not answers_member(_conversation(ask=_ask(AskMode.LINK)))


def test_no_conversation_is_no() -> None:
    assert not answers_member(None)
