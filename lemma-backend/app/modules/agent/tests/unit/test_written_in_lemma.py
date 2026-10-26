"""A message typed in Lemma into a group's conversation, and who the model takes it for.

In the thread where the bot answers people outside the pod, the member who looks
after the group typed "ask him what's up with him". The line carried no sender
label, the brief said everyone writing was a stranger, and the model read it as
the stranger's -- so it passed the question on to the member it came from, and
told the group it had. (Then with ``message_user``; a contact's run now calls a
person in with ``ask_a_person``, which tells that same member.) These pin each place that
now says otherwise: the server's stamp, the history's label, the run's mark, the
refused tool call, the brief, and the group section of the prompt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent.tests.unit.repository_fakes import wire_repositories
from app.modules.agent.tests.unit.work_recording import RecordingWorkSession

from app.modules.agent.domain.entities import AgentRun, Conversation, Message
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.private_notes import (
    PRIVATE_NOTE_FROM_KEEPER_LABEL,
    PRIVATE_NOTE_IN_DM_LABEL,
    PRIVATE_NOTE_LABEL,
    PRIVATE_NOTE_REQUEST_KEY,
    WRITTEN_BY_KEEPER_LABEL,
    WRITTEN_IN_LEMMA_LABEL,
    keeper_started,
    lemma_label,
    run_state_for,
    written_in_lemma_into,
)
from app.modules.agent.domain.prompt.surface import (
    is_group_conversation,
    surface_platform_guidance,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRuntimeConfig,
    WrittenIn,
)
from app.modules.agent.domain.prompt.user_turn_text import (
    user_prompt_text,
)
from app.modules.agent.application.turns import admit as turns
from app.modules.agent.services.context_brief.outsider import render_outsider_brief
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.messaging.models import AskAPersonRequest
from app.modules.agent.infrastructure.tools.messaging.tools import (
    ask_a_person,
)

pytestmark = pytest.mark.unit

#: A contact's conversation in a group: somebody outside the pod asking there.
_OUTSIDERS_THREAD = {"surface_platform": "TELEGRAM", "conversation_kind": "CHANNEL"}
_MEMBERS_GROUP = {"surface_platform": "TELEGRAM", "conversation_kind": "CHANNEL"}
_DIRECT_CHAT = {"surface_platform": "TELEGRAM", "conversation_kind": "DM"}
#: A contact's private chat: a DM on the platform, with somebody outside the pod.
_CONTACT_CHAT = {"surface_platform": "WHATSAPP", "conversation_kind": "DM"}


# ------------------------------------------------------------------ the stamp


@pytest.mark.parametrize(
    ("conversation_metadata", "contacts", "expected"),
    [
        (_OUTSIDERS_THREAD, True, WrittenIn.OUTSIDERS),
        (_MEMBERS_GROUP, False, WrittenIn.GROUP),
        (_DIRECT_CHAT, False, None),
        (_CONTACT_CHAT, True, WrittenIn.OUTSIDERS),
        ({"surface_platform": "RESEND", "conversation_kind": "EMAIL"}, False, None),
        ({}, False, None),
        (None, False, None),
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
    conversation_metadata, contacts, expected
):
    conversation = SimpleNamespace(
        metadata=conversation_metadata, contact_id=uuid4() if contacts else None
    )

    assert written_in_lemma_into(conversation) == expected


def _runtime() -> AgentRuntimeConfig:
    return AgentRuntimeConfig(profile_id="user:harness", model_name="claude-sonnet-4-5")


def _coordinator(conversation: Conversation):
    """A use case that records the run it creates and the message it saves."""
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
            state=draft.state,
        )

    repository = SimpleNamespace(
        lock_conversation=AsyncMock(),
        get_active_agent_run_for_update=AsyncMock(return_value=None),
        create_agent_run=AsyncMock(return_value=created),
        append_message=AsyncMock(side_effect=append_message),
        # No pause is open, so nothing holds the message back.
        pausing_calls_decided_awaiting_a_return=AsyncMock(return_value=[]),
    )
    agents = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(name="lem", kind=None, agent_runtime=None)
        )
    )
    uow = SimpleNamespace(
        collect_events=MagicMock(),
        commit=AsyncMock(),
        after_commit=MagicMock(),
        # The pod names no default runtime of its own.
        session=RecordingWorkSession(),
    )
    coordinator = turns.AdmitMessage(
        uow,
        agent_repository=agents,
        supersede=SimpleNamespace(supersede=AsyncMock(return_value=[])),
        usage_service=None,
        runtime_check=_runtime_is_available,
    )
    return wire_repositories(coordinator, repository), repository


async def _runtime_is_available(*_args: object, **_kwargs: object) -> None:
    """These tests are about the message's stamp, not the model it runs on."""


def _conversation(metadata: dict, *, contacts: bool = False) -> Conversation:
    return Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        contact_id=uuid4() if contacts else None,
        organization_id=uuid4(),
        agent_id=uuid4(),
        agent_runtime=_runtime(),
        metadata=metadata,
    )


async def _start(conversation, *, metadata, typed_in_lemma):
    coordinator, repository = _coordinator(conversation)
    await coordinator.admit(
        conversation,
        user_id=conversation.user_id,
        content="ask him what's up with him",
        agent_name=None,
        message_metadata=metadata,
        typed_in_lemma=typed_in_lemma,
    )
    saved = repository.append_message.await_args.kwargs["draft"]
    run = repository.create_agent_run.await_args.kwargs["state"]
    return saved, run


async def test_the_keepers_message_and_its_run_are_marked_by_the_server():
    saved, run = await _start(
        _conversation(dict(_OUTSIDERS_THREAD), contacts=True),
        metadata=None,
        typed_in_lemma=True,
    )

    assert saved.state.written_in is WrittenIn.OUTSIDERS
    assert keeper_started(run)


async def test_a_chat_platform_cannot_claim_its_line_was_typed_in_lemma():
    """A stranger's line taken for the member's would be answered as theirs."""
    saved, run = await _start(
        _conversation(dict(_OUTSIDERS_THREAD), contacts=True),
        metadata={
            "surface_platform": "TELEGRAM",
            "sender_display_name": "P C",
            "written_in_lemma": "outsiders",
        },
        typed_in_lemma=False,
    )

    assert "written_in_lemma" not in saved.metadata
    assert saved.state.written_in is None
    assert not keeper_started(run)


async def test_a_sender_cannot_mark_its_message_as_a_history_notice():
    """`synthetic` is what a notice history assembly writes carries, and such a
    notice is read as Lemma's own, its label lines left as they are. It is never
    stored, so a sender that sends it is imitating one."""
    saved, _ = await _start(
        _conversation(dict(_DIRECT_CHAT)),
        metadata={"synthetic": True},
        typed_in_lemma=True,
    )

    assert "synthetic" not in saved.metadata


async def test_typing_in_lemma_into_a_direct_chat_marks_nothing():
    """The person typing is the person the bot is talking to there."""
    saved, run = await _start(
        _conversation(dict(_DIRECT_CHAT)),
        metadata={PRIVATE_NOTE_REQUEST_KEY: True},
        typed_in_lemma=True,
    )

    assert saved.state.written_in is None
    assert run.written_in is None


async def test_a_note_in_a_members_own_dm_is_labelled_as_theirs():
    saved, _ = await _start(
        _conversation(dict(_DIRECT_CHAT)),
        metadata={PRIVATE_NOTE_REQUEST_KEY: True},
        typed_in_lemma=True,
    )

    assert saved.state.private_note_in_dm is True
    assert lemma_label(saved.state) == PRIVATE_NOTE_IN_DM_LABEL


async def test_a_note_in_a_contacts_dm_is_the_keepers_never_the_contacts():
    """The person in a contact's chat is the contact. A note typed there in
    Lemma was labelled as theirs -- "by the person in this chat" -- so the
    model took the keeper's private words as the contact's own."""
    saved, run = await _start(
        _conversation(dict(_CONTACT_CHAT), contacts=True),
        metadata={PRIVATE_NOTE_REQUEST_KEY: True},
        typed_in_lemma=True,
    )

    assert saved.state.private_note_in_dm is False
    assert saved.state.written_in is WrittenIn.OUTSIDERS
    assert lemma_label(saved.state) == PRIVATE_NOTE_FROM_KEEPER_LABEL
    assert keeper_started(run)


async def test_a_plain_message_typed_into_a_contacts_dm_is_the_keepers():
    saved, run = await _start(
        _conversation(dict(_CONTACT_CHAT), contacts=True),
        metadata=None,
        typed_in_lemma=True,
    )

    assert lemma_label(saved.state) == WRITTEN_BY_KEEPER_LABEL
    assert keeper_started(run)


def test_a_dm_stamp_never_relabels_a_note_in_a_thread_answering_outsiders():
    """A row stamped before contacts counted as outsiders keeps the keeper's
    label: the thread decides whose words they were, not the stamp."""
    note = MessageState(
        private_note=True, private_note_in_dm=True, written_in=WrittenIn.OUTSIDERS
    )

    assert lemma_label(note) == PRIVATE_NOTE_FROM_KEEPER_LABEL


def test_a_resume_or_retry_keeps_answering_the_keeper():
    marks = run_state_for(MessageState(written_in=WrittenIn.OUTSIDERS)).privacy

    assert keeper_started(marks)
    assert not keeper_started(run_state_for(MessageState()).privacy)


# ------------------------------------------------------------------ the label


@pytest.mark.parametrize(
    ("state", "label"),
    [
        (MessageState(written_in=WrittenIn.OUTSIDERS), WRITTEN_BY_KEEPER_LABEL),
        (
            MessageState(written_in=WrittenIn.OUTSIDERS, private_note=True),
            PRIVATE_NOTE_FROM_KEEPER_LABEL,
        ),
        (MessageState(written_in=WrittenIn.GROUP), WRITTEN_IN_LEMMA_LABEL),
        (
            MessageState(written_in=WrittenIn.GROUP, private_note=True),
            PRIVATE_NOTE_LABEL,
        ),
        (MessageState(), None),
    ],
    ids=["keeper-reply", "keeper-note", "group-reply", "group-note", "plain"],
)
def test_the_history_says_who_typed_it_and_that_the_chat_did_not_see_it(state, label):
    assert lemma_label(state) == label


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
            metadata={},
            state=MessageState(written_in=WrittenIn.OUTSIDERS),
        )
    )

    assert strangers.startswith("[TELEGRAM | P C]:")
    assert keepers.startswith(WRITTEN_BY_KEEPER_LABEL)
    assert "member who looks after this conversation" in keepers


# ------------------------------------------------------------- ask_a_person


def _deps(*, keeper_asking: bool) -> ToolContext:
    return ToolContext(
        user_id=uuid4(),
        org_id=uuid4(),
        pod_id=uuid4(),
        conversation_id=uuid4(),
        audience=Audience.contact(uuid4()),
        keeper_asking=keeper_asking,
    )


async def test_the_keepers_own_question_is_never_passed_back_to_them(monkeypatch):
    """The member who typed it is the person `ask_a_person` would call in.
    Flagging the channel and telling them a contact needs a person reports
    their own words back to them as a stranger's, while the group is told
    somebody will get back to it."""
    flagged: list[dict] = []

    async def _ask(**kwargs):
        flagged.append(kwargs)
        return True

    monkeypatch.setattr(
        "app.modules.agent.infrastructure.tools.messaging.people.call_a_person_in", _ask
    )

    result = await ask_a_person(
        _deps(keeper_asking=True).model_copy(update={"tool_call_id": "call-1"}),
        AskAPersonRequest(reason="P C asks what's up with you"),
    )

    assert result.success is False
    assert flagged == []


async def test_a_strangers_run_still_calls_the_keeper_in(monkeypatch):
    """The refusal above is about who typed the line, not about the tool."""
    flagged: list[dict] = []

    async def _ask(**kwargs):
        flagged.append(kwargs)
        return True

    monkeypatch.setattr(
        "app.modules.agent.infrastructure.tools.messaging.people.call_a_person_in", _ask
    )

    result = await ask_a_person(
        _deps(keeper_asking=False).model_copy(update={"tool_call_id": "call-1"}),
        AskAPersonRequest(reason="Wants the price list."),
    )

    assert result.success is True
    assert len(flagged) == 1


# ---------------------------------------------------------------- the brief


def test_the_strangers_brief_says_a_lemma_line_is_the_keepers():
    brief = render_outsider_brief(pod_name="Sales", owner_display_name="Priya")

    assert "written in Lemma is from Priya" in brief
    assert "Answer it here" in brief


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
