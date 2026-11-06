"""How a conversation is listed, read off its typed origin.

The Postgres twin is held to this rule in ``test_listed_origin_sql_agrees``.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.listed_origin import (
    BY_ORIGIN,
    CHATS,
    STARTED_BY_AN_AGENT,
    ListedOrigin,
    listed_origin_of,
)
from app.modules.agent.domain.state_kinds import ConversationOrigin

pytestmark = pytest.mark.unit

Origin = ConversationOrigin

#: Every stored shape the rule distinguishes, and how it is listed. Shared with
#: the SQL agreement test so the two are run over exactly the same cases.
SHAPES: list[tuple[ConversationOrigin, str, dict[str, object] | None, ListedOrigin]] = [
    (Origin.CHAT, "CHAT", None, ListedOrigin.CHAT),
    (Origin.CHAT, "CHAT", {}, ListedOrigin.CHAT),
    (Origin.SURFACE, "CHAT", None, ListedOrigin.CHANNEL),
    (Origin.CHANNEL, "CHAT", None, ListedOrigin.CHANNEL),
    (Origin.CONTACT, "CHAT", None, ListedOrigin.CHANNEL),
    (Origin.NOTIFICATION, "TASK", None, ListedOrigin.NOTIFICATION),
    (Origin.SCHEDULE, "TASK", {"schedule_name": "daily"}, ListedOrigin.SCHEDULE),
    (Origin.POD_ASK, "TASK", None, ListedOrigin.ASK),
    (Origin.WORKFLOW_STEP, "TASK", None, ListedOrigin.WORKFLOW),
    (Origin.SUBAGENT, "CHAT", None, ListedOrigin.TASK),
    (
        Origin.CHAT,
        "CHAT",
        {"lemma_resource": "file:/notes/plan.md"},
        ListedOrigin.RESOURCE,
    ),
    # A blank or non-string resource binds nothing.
    (Origin.CHAT, "CHAT", {"lemma_resource": "   "}, ListedOrigin.CHAT),
    (Origin.CHAT, "TASK", {"lemma_resource": 7}, ListedOrigin.TASK),
    # A channel's origin outranks a bound resource.
    (Origin.SURFACE, "CHAT", {"lemma_resource": "file:/a"}, ListedOrigin.CHANNEL),
    # What the column does not decide falls through to the type.
    (Origin.PROJECT, "PROJECT", None, ListedOrigin.CHAT),
    (Origin.CALL, "CHAT", None, ListedOrigin.CHAT),
    (Origin.SETUP, "CHAT", None, ListedOrigin.CHAT),
    (Origin.CHAT, "TASK", None, ListedOrigin.TASK),
]


@pytest.mark.parametrize(
    ("origin", "conversation_type", "metadata", "expected"), SHAPES
)
def test_an_origin_is_listed_by_its_column(
    origin, conversation_type, metadata, expected
):
    assert listed_origin_of(origin, conversation_type, metadata) is expected


def test_every_origin_is_covered():
    assert {origin for origin, *_ in SHAPES} == set(ConversationOrigin)


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
    assert CHATS == {ListedOrigin.CHAT, ListedOrigin.CHANNEL}
    assert not CHATS & STARTED_BY_AN_AGENT


def test_no_origin_the_column_decides_is_a_resource_or_a_chat():
    """Those two are the fall-through's, read off the metadata and the type."""
    assert not {ListedOrigin.RESOURCE, ListedOrigin.CHAT} & set(BY_ORIGIN.values())
