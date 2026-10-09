"""A message typed in Lemma into a group's conversation, and who the model takes it for.

In the thread where the bot answers people outside the pod, the member who looks
after the group typed "ask him what's up with him". The line carried no sender
label, the brief said everyone writing was a stranger, and the model read it as
the stranger's -- so it passed the question on with ``message_user``, which only
reaches that same member, and told the group it had. These pin each place that
now says otherwise: the server's stamp, the history's label, the run's mark, the
refused tool call, the brief, and the group section of the prompt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import AgentRun, Conversation, Message
from app.modules.agent.domain.outsiders import AUDIENCE_KEY, OUTSIDERS, Audience
from app.modules.agent.domain.private_notes import (
    IN_GROUP,
    IN_OUTSIDERS_THREAD,
    NOTE_IN_DM_KEY,
    PRIVATE_NOTE_FROM_KEEPER_LABEL,
    PRIVATE_NOTE_IN_DM_LABEL,
    PRIVATE_NOTE_KEY,
    PRIVATE_NOTE_LABEL,
    WRITTEN_BY_KEEPER_LABEL,
    WRITTEN_IN_LEMMA_KEY,
    WRITTEN_IN_LEMMA_LABEL,
    keeper_started,
    lemma_label,
    privacy_of,
    run_metadata_for,
    written_in_lemma_into,
)
from app.modules.agent.domain.surface_prompts import (
    is_group_conversation,
    surface_platform_guidance,
)
from app.modules.agent.domain.value_objects import AgentRunStatus, AgentRuntimeConfig
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    user_prompt_text,
)
from app.modules.agent.services import conversation_turns as turns
from app.modules.agent.services.outsider_brief import render_outsider_brief
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.models import MessageUserRequest
from app.modules.agent.tools.messaging.pydantic_adapter import message_user

pytestmark = pytest.mark.unit

_OUTSIDERS_THREAD = {
    AUDIENCE_KEY: OUTSIDERS,
    "surface_platform": "TELEGRAM",
    "conversation_kind": "CHANNEL",
}
_MEMBERS_GROUP = {"surface_platform": "TELEGRAM", "conversation_kind": "CHANNEL"}
_DIRECT_CHAT = {"surface_platform": "TELEGRAM", "conversation_kind": "DM"}
#: A contact's private chat: a DM on the platform, with somebody outside the pod.
_CONTACT_CHAT = {
    **Audience.contact(uuid4()).to_metadata(),
    "surface_platform": "WHATSAPP",
    "conversation_kind": "DM",
}


# ------------------------------------------------------------------ the stamp


@pytest.mark.parametrize(
    ("conversation_metadata", "expected"),
    [
        (_OUTSIDERS_THREAD, IN_OUTSIDERS_THREAD),
        (_MEMBERS_GROUP, IN_GROUP),
        (_DIRECT_CHAT, None),
        (_CONTACT_CHAT, IN_OUTSIDERS_THREAD),
        ({"surface_platform": "RESEND", "conversation_kind": "EMAIL"}, None),
        ({}, None),
        (None, None),
    ],
    ids=[
        "outsiders",
        "members-group",
        "dm",
        "contact-dm",
        "email",
        "lemma-only",
        "no-metadata",
    ],
)
def test_only_a_groups_conversation_marks_what_is_typed_into_it(
    conversation_metadata, expected
):
    assert written_in_lemma_into(conversation_metadata) == expected


def _runtime() -> AgentRuntimeConfig:
    return AgentRuntimeConfig(profile_id="user:harness", model_name="claude-sonnet-4-5")


def _coordinator(conversation: Conversation):
    """A coordinator that records the run it creates and the message it saves."""
    created = AgentRun(
        conversation_id=conversation.id,
        status=AgentRunStatus.RUNNING,
        agent_runtime=_runtime(),
        started_at=datetime.now(timezone.utc),
    )

    async def append_message(*, conversation_id, agent_run_id, draft):
        return Message(
            conversation_id=conversation_id,
            sequence=1,
            agent_run_id=agent_run_id,
            role=draft.role,
            kind=draft.kind,
            text=draft.text,
            metadata=draft.metadata,
        )

    repository = SimpleNamespace(
        lock_conversation=AsyncMock(),
        get_active_agent_run_for_update=AsyncMock(return_value=None),
        create_agent_run=AsyncMock(return_value=created),
        append_message=AsyncMock(side_effect=append_message),
    )
    agents = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(name="lem", kind=None))
    )
    uow = SimpleNamespace(
        collect_events=MagicMock(), commit=AsyncMock(), after_commit=MagicMock()
    )
    coordinator = turns.TurnCoordinator(
        uow,
        repository,
        agents,
        SimpleNamespace(
            supersede_stale_pending_interactions=AsyncMock(return_value=[])
        ),
        SimpleNamespace(),
        None,
    )
    return coordinator, repository


def _conversation(metadata: dict) -> Conversation:
    return Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        organization_id=uuid4(),
        agent_id=uuid4(),
        agent_runtime=_runtime(),
        metadata=metadata,
    )


async def _start(conversation, *, metadata, typed_in_lemma):
    coordinator, repository = _coordinator(conversation)
    await coordinator.start(
        conversation,
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
        content="ask him what's up with him",
        agent_name=None,
        message_metadata=metadata,
        typed_in_lemma=typed_in_lemma,
    )
    saved = repository.append_message.await_args.kwargs["draft"].metadata
    run = repository.create_agent_run.await_args.kwargs["metadata"]
    return saved, run


async def test_the_keepers_message_and_its_run_are_marked_by_the_server():
    saved, run = await _start(
        _conversation(dict(_OUTSIDERS_THREAD)),
        metadata=None,
        typed_in_lemma=True,
    )

    assert saved[WRITTEN_IN_LEMMA_KEY] == IN_OUTSIDERS_THREAD
    assert keeper_started(run)


async def test_a_chat_platform_cannot_claim_its_line_was_typed_in_lemma():
    """A stranger's line taken for the member's would be answered as theirs."""
    saved, run = await _start(
        _conversation(dict(_OUTSIDERS_THREAD)),
        metadata={
            "surface_platform": "TELEGRAM",
            "sender_display_name": "P C",
            WRITTEN_IN_LEMMA_KEY: IN_OUTSIDERS_THREAD,
        },
        typed_in_lemma=False,
    )

    assert WRITTEN_IN_LEMMA_KEY not in saved
    assert not keeper_started(run)


async def test_typing_in_lemma_into_a_direct_chat_marks_nothing():
    """The person typing is the person the bot is talking to there."""
    saved, run = await _start(
        _conversation(dict(_DIRECT_CHAT)),
        metadata={PRIVATE_NOTE_KEY: True},
        typed_in_lemma=True,
    )

    assert WRITTEN_IN_LEMMA_KEY not in saved
    assert WRITTEN_IN_LEMMA_KEY not in run


async def test_a_note_in_a_members_own_dm_is_labelled_as_theirs():
    saved, _ = await _start(
        _conversation(dict(_DIRECT_CHAT)),
        metadata={PRIVATE_NOTE_KEY: True},
        typed_in_lemma=True,
    )

    assert saved[NOTE_IN_DM_KEY] is True
    assert lemma_label(saved) == PRIVATE_NOTE_IN_DM_LABEL


async def test_a_note_in_a_contacts_dm_is_the_keepers_never_the_contacts():
    """The person in a contact's chat is the contact. A note typed there in
    Lemma was labelled as theirs -- "by the person in this chat" -- so the
    model took the keeper's private words as the contact's own."""
    saved, run = await _start(
        _conversation(dict(_CONTACT_CHAT)),
        metadata={PRIVATE_NOTE_KEY: True},
        typed_in_lemma=True,
    )

    assert NOTE_IN_DM_KEY not in saved
    assert saved[WRITTEN_IN_LEMMA_KEY] == IN_OUTSIDERS_THREAD
    assert lemma_label(saved) == PRIVATE_NOTE_FROM_KEEPER_LABEL
    assert keeper_started(run)


async def test_a_plain_message_typed_into_a_contacts_dm_is_the_keepers():
    saved, run = await _start(
        _conversation(dict(_CONTACT_CHAT)),
        metadata=None,
        typed_in_lemma=True,
    )

    assert lemma_label(saved) == WRITTEN_BY_KEEPER_LABEL
    assert keeper_started(run)


def test_a_dm_stamp_never_relabels_a_note_in_a_thread_answering_outsiders():
    """A row stamped before contacts counted as outsiders keeps the keeper's
    label: the thread decides whose words they were, not the stamp."""
    note = {
        PRIVATE_NOTE_KEY: True,
        NOTE_IN_DM_KEY: True,
        WRITTEN_IN_LEMMA_KEY: IN_OUTSIDERS_THREAD,
    }

    assert lemma_label(note) == PRIVATE_NOTE_FROM_KEEPER_LABEL


def test_a_resume_or_retry_keeps_answering_the_keeper():
    marks = privacy_of(run_metadata_for({WRITTEN_IN_LEMMA_KEY: IN_OUTSIDERS_THREAD}))

    assert keeper_started(marks)
    assert not keeper_started(privacy_of(run_metadata_for({})))


# ------------------------------------------------------------------ the label


@pytest.mark.parametrize(
    ("metadata", "label"),
    [
        ({WRITTEN_IN_LEMMA_KEY: IN_OUTSIDERS_THREAD}, WRITTEN_BY_KEEPER_LABEL),
        (
            {WRITTEN_IN_LEMMA_KEY: IN_OUTSIDERS_THREAD, PRIVATE_NOTE_KEY: True},
            PRIVATE_NOTE_FROM_KEEPER_LABEL,
        ),
        ({WRITTEN_IN_LEMMA_KEY: IN_GROUP}, WRITTEN_IN_LEMMA_LABEL),
        ({WRITTEN_IN_LEMMA_KEY: IN_GROUP, PRIVATE_NOTE_KEY: True}, PRIVATE_NOTE_LABEL),
        ({}, None),
        ({WRITTEN_IN_LEMMA_KEY: "somewhere"}, None),
    ],
    ids=[
        "keeper-reply",
        "keeper-note",
        "group-reply",
        "group-note",
        "plain",
        "unknown-value",
    ],
)
def test_the_history_says_who_typed_it_and_that_the_chat_did_not_see_it(
    metadata, label
):
    assert lemma_label(metadata) == label


def test_the_keepers_line_reads_as_theirs_beside_a_strangers():
    strangers = user_prompt_text(
        SimpleNamespace(
            text="what all do we have",
            metadata={"surface_platform": "TELEGRAM", "sender_display_name": "P C"},
        )
    )
    keepers = user_prompt_text(
        SimpleNamespace(
            text="ask him what's up with him",
            metadata={WRITTEN_IN_LEMMA_KEY: IN_OUTSIDERS_THREAD},
        )
    )

    assert strangers.startswith("[TELEGRAM | P C]:")
    assert keepers.startswith(WRITTEN_BY_KEEPER_LABEL)
    assert "member who looks after this conversation" in keepers


# ------------------------------------------------------------- message_user


def _deps(*, keeper_asking: bool) -> BaseAgentContext:
    return BaseAgentContext(
        user_id=uuid4(),
        org_id=uuid4(),
        pod_id=uuid4(),
        conversation_id=uuid4(),
        audience=Audience.outsiders(),
        keeper_asking=keeper_asking,
    )


async def test_the_keepers_own_question_is_never_passed_back_to_them():
    result = await message_user(
        SimpleNamespace(deps=_deps(keeper_asking=True), tool_call_id="call-1"),
        MessageUserRequest(to="Deepak", message="P C asks what's up with you"),
    )

    assert result.success is False
    assert "Answer in your reply" in (result.error or "")


# ---------------------------------------------------------------- the brief


def test_the_strangers_brief_says_a_lemma_line_is_the_keepers():
    brief = render_outsider_brief(pod_name="Sales", owner_display_name="Priya")

    assert "written in Lemma is from Priya" in brief
    assert "never pass it on to them" in brief


# ---------------------------------------------------------------- the group


def test_a_group_is_told_it_is_a_group():
    text = surface_platform_guidance("TELEGRAM", in_group=True)

    assert "## In a group" in text
    assert "conversing with people in a group chat" in text
    assert "written in Lemma was not seen by the group" in text


def test_a_direct_chat_is_not():
    text = surface_platform_guidance("TELEGRAM")

    assert "## In a group" not in text
    assert "conversing with the user" in text


def test_email_keeps_its_own_shape_whatever_the_kind_says():
    assert "## In a group" not in surface_platform_guidance("RESEND", in_group=True)


def test_a_group_is_a_channel_conversation():
    assert is_group_conversation(SimpleNamespace(surface_conversation_kind="CHANNEL"))
    assert not is_group_conversation(SimpleNamespace(surface_conversation_kind="DM"))
    assert not is_group_conversation(SimpleNamespace())
