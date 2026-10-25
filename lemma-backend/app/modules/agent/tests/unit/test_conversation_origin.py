"""Where a conversation came from, read off what it was stamped with.

The rule is shared with the web client, which draws the rows a day's count is
made of; the Postgres twin is held to this one in
``test_conversation_origin_sql_agrees``.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.conversation_origin import (
    CHATS,
    STARTED_BY_AN_AGENT,
    ConversationOrigin,
    origin_of,
)

pytestmark = pytest.mark.unit

#: Every stored shape the rule distinguishes, and what it reads as. Shared with
#: the SQL agreement test so the two are run over exactly the same cases.
SHAPES: list[tuple[dict[str, object] | None, str, ConversationOrigin]] = [
    (None, "CHAT", ConversationOrigin.CHAT),
    ({}, "CHAT", ConversationOrigin.CHAT),
    ({"source": "agent_surfaces"}, "CHAT", ConversationOrigin.CHANNEL),
    (
        {"source": "notification", "surface_platform": "SLACK"},
        "TASK",
        ConversationOrigin.NOTIFICATION,
    ),
    (
        {"source": "SCHEDULE", "schedule_name": "daily"},
        "TASK",
        ConversationOrigin.SCHEDULE,
    ),
    ({"source": "pod_ask"}, "TASK", ConversationOrigin.ASK),
    ({"source": "WORKFLOW_RUN"}, "TASK", ConversationOrigin.WORKFLOW),
    # The outsiders' group reads as a channel whatever opened it.
    (
        {"audience": "outsiders", "source": "SCHEDULE"},
        "CHAT",
        ConversationOrigin.CHANNEL,
    ),
    ({"lemma_resource": "file:/notes/plan.md"}, "CHAT", ConversationOrigin.RESOURCE),
    # A blank or non-string resource binds nothing.
    ({"lemma_resource": "   "}, "CHAT", ConversationOrigin.CHAT),
    ({"lemma_resource": 7}, "TASK", ConversationOrigin.TASK),
    # A source this rule does not know falls through to the type.
    ({"source": "manual_retry"}, "TASK", ConversationOrigin.TASK),
    ({"source": "schedule"}, "CHAT", ConversationOrigin.CHAT),
    (None, "TASK", ConversationOrigin.TASK),
]


@pytest.mark.parametrize(("metadata", "conversation_type", "expected"), SHAPES)
def test_an_origin_is_read_off_its_stamp(metadata, conversation_type, expected):
    assert origin_of(metadata, conversation_type) is expected


def test_a_channel_source_outranks_a_bound_resource():
    assert (
        origin_of({"source": "agent_surfaces", "lemma_resource": "file:/a"}, "CHAT")
        is ConversationOrigin.CHANNEL
    )


def test_what_an_agent_starts_is_what_the_client_counts_as_lemmas_work():
    """The web client's ``STARTED_BY_LEMMA``: the bars split on exactly these."""
    assert {origin.value for origin in STARTED_BY_AN_AGENT} == {
        "schedule",
        "workflow",
        "notification",
        "task",
        "ask",
    }


def test_chats_are_what_a_person_talks_in():
    assert CHATS == {ConversationOrigin.CHAT, ConversationOrigin.CHANNEL}
    assert not CHATS & STARTED_BY_AN_AGENT
