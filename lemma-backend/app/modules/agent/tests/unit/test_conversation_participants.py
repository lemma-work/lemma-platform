"""Who may reach a conversation once it can hold more than one person.

The access rule is the behaviour change: it used to be equality against
`conversation.user_id`, and it is now that or membership. These pin both halves
plus the cases that would quietly widen it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.errors import ConversationNotFoundError
from app.modules.agent.domain.participants import (
    ConversationParticipant,
    ConversationParticipantRole,
)
from app.modules.agent.infrastructure.conversation_participant_store import (
    add_participant,
    remove_participant,
)
from app.modules.agent.infrastructure.participant_models import (
    ConversationParticipantModel,
)
from app.modules.agent.services.conversation_access import (
    validate_conversation_access,
)
from app.modules.test_support.mappers import configure_test_mappers

# Constructing a participant model configures the mappers, and a partial model
# graph fails to resolve its relationship targets by name — so without this the
# file passes in a suite and fails on its own.
configure_test_mappers()


def _conversation(**kwargs) -> Conversation:
    return Conversation(user_id=uuid4(), pod_id=uuid4(), **kwargs)


def test_owner_reaches_their_own_conversation():
    conversation = _conversation()
    validate_conversation_access(
        conversation, user_id=conversation.user_id, pod_id=conversation.pod_id
    )


def test_stranger_does_not():
    conversation = _conversation()
    with pytest.raises(ConversationNotFoundError):
        validate_conversation_access(
            conversation, user_id=uuid4(), pod_id=conversation.pod_id
        )


def test_added_member_reaches_it():
    conversation = _conversation()
    member_id = uuid4()
    conversation.participants = [
        ConversationParticipant(conversation_id=conversation.id, user_id=member_id)
    ]
    validate_conversation_access(
        conversation, user_id=member_id, pod_id=conversation.pod_id
    )


def test_membership_does_not_cross_pods():
    """Membership widens *who*, never *where*. A member reaching in from the
    wrong pod is still nothing, or a conversation would be readable through any
    pod its member happens to belong to."""
    conversation = _conversation()
    member_id = uuid4()
    conversation.participants = [
        ConversationParticipant(conversation_id=conversation.id, user_id=member_id)
    ]
    with pytest.raises(ConversationNotFoundError):
        validate_conversation_access(conversation, user_id=member_id, pod_id=uuid4())


def test_an_agent_participant_grants_nobody_access():
    """Agent rows share the table, and their `user_id` is NULL. A membership
    test written as "is there a row for you" would match every person against
    every agent row if it ever saw a null id on both sides."""
    conversation = _conversation()
    conversation.participants = [
        ConversationParticipant(conversation_id=conversation.id, agent_id=uuid4())
    ]
    assert conversation.has_participant(uuid4()) is False
    with pytest.raises(ConversationNotFoundError):
        validate_conversation_access(
            conversation, user_id=uuid4(), pod_id=conversation.pod_id
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"user_id": uuid4(), "agent_id": uuid4()},
    ],
    ids=["neither", "both"],
)
async def test_a_participant_is_exactly_one_subject(kwargs):
    """Rejected before the session is touched, which is why None works here."""
    with pytest.raises(ValueError):
        await add_participant(None, conversation_id=uuid4(), **kwargs)
    with pytest.raises(ValueError):
        await remove_participant(None, conversation_id=uuid4(), **kwargs)


class _SessionHolding:
    def __init__(self, model):
        self._model = model

    async def scalar(self, _statement):
        return self._model


async def test_the_owner_cannot_be_removed():
    conversation_id, owner_id = uuid4(), uuid4()
    session = _SessionHolding(
        ConversationParticipantModel(
            id=uuid4(),
            conversation_id=conversation_id,
            user_id=owner_id,
            role=ConversationParticipantRole.OWNER.value,
        )
    )
    with pytest.raises(ValueError):
        await remove_participant(
            session, conversation_id=conversation_id, user_id=owner_id
        )


class _SessionHoldingNothing:
    async def scalar(self, _statement):
        return None


async def test_removing_somebody_who_is_not_there_is_not_an_error():
    assert (
        await remove_participant(
            _SessionHoldingNothing(), conversation_id=uuid4(), user_id=uuid4()
        )
        is False
    )


# --- addressing an agent present in the conversation -------------------------


def test_an_agent_added_to_the_conversation_may_be_addressed():
    """An `@mention` names an agent for one turn. Being present in the
    conversation is as good a claim to answer as being its default agent."""
    conversation = _conversation(agent_id=uuid4())
    guest_agent_id = uuid4()
    conversation.participants = [
        ConversationParticipant(
            conversation_id=conversation.id, agent_id=guest_agent_id
        )
    ]

    validate_conversation_access(
        conversation,
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
        agent_id=guest_agent_id,
    )


def test_an_agent_that_is_not_here_may_not_be_addressed():
    """Otherwise a name typed into a conversation reaches an agent nobody
    added to it, with that conversation's history."""
    conversation = _conversation(agent_id=uuid4())

    with pytest.raises(ConversationNotFoundError):
        validate_conversation_access(
            conversation,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
            agent_id=uuid4(),
        )
