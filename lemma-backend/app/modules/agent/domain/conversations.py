"""Who owns which part of a conversation's metadata, and which keys left it.

A conversation's metadata is its client's tags: the web app writes
``lemma_resource`` and ``lemma_attached_file`` there, an SDK user any key of
their own, and the list filter ``?metadata=`` reads them back. Everything the
server decides on is a typed column of the row, which no request reaches
(``Conversation`` documents each).

``SERVER_READ_KEYS`` are the keys the server still reads out of metadata,
until they move to tables of their own -- a chat surface's binding
(agent_surfaces' contracts phase).
A client can neither set nor drop them: they are taken out of what it sends,
and an update keeps whatever the server wrote.

``MOVED_*_KEYS`` were metadata once and are columns, or the Agent Host's own
tables, now. A client may still send one -- it is a tag like any other, and the
list filter finds it -- but it changes nothing, because the column is what is
read. These lists are also what
``tests/unit/test_moved_metadata_keys`` holds every ``*_metadata`` read and
write in the module against.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.modules.agent.domain.value_objects import ConversationOrigin, JsonObject

#: Keys of a conversation's metadata the server reads, which no client writes.
SERVER_READ_KEYS = frozenset(
    {
        # A chat surface's binding (`agent_surfaces.services.conversation_binder`),
        # and the records agent_surfaces keeps on the row.
        "surface_id",
        "surface_platform",
        "surface_event_metadata",
        "surface_held_replies",
        "surface_free_text_answer_for",
        "external_user_id",
        "external_channel_id",
        "external_thread_id",
        "external_message_id",
        "channel_name",
        "conversation_kind",
        "route_key",
        "routed_agent_id",
        "web_widget_id",
        "web_session_id",
        # What fired a schedule, which the run's platform origin is read from.
        "schedule_type",
    }
)

#: Conversation metadata keys that are columns now.
MOVED_CONVERSATION_KEYS = frozenset(
    {
        "source",
        "is_sub_agent",
        "spawned_by_agent",
        "self_spawned",
        "workflow_run_id",
        "started_by",
        "parent_run_id",
        "spawn_key",
        "reports_to_parent",
        "subagents_out",
        "pod_asks_out",
        "ask",
        "call",
        "cwd",
        "workspace",
        "workspace_id",
        "workspace_name",
        "repo",
        "repo_full_name",
        "todos",
        # The Agent Host's session: `agent_host_sessions`.
        "agent_host_provider_session_id",
    }
)

#: Run metadata keys that are columns now.
MOVED_RUN_KEYS = frozenset(
    {
        "source",
        "private_note",
        "asked_in_lemma",
        "written_in_lemma",
        "resumed_tool_call_id",
        "retried_agent_run_id",
        "queued_behind_agent_run_id",
        "queued_leftovers_behind",
        "stop_requested_after_sequence",
        "collected_by_parent",
        "suspended_while_parked",
        "schedule_id",
        "schedule_name",
        "schedule_type",
        "trigger_type",
        "fired_at",
        "scheduled_for",
        "timezone",
        "failure",
        # An Agent Host run's record: `agent_host_run_records`.
        "execution",
        "host_native_commands",
        "final_answer",
    }
)

#: Message metadata keys that are columns now, and the diagnostics nothing read.
MOVED_MESSAGE_KEYS = frozenset(
    {
        "during_active_run",
        "steered_into_run",
        "steer_dispatched_at",
        "steer_undelivered",
        "waiting_on_answer",
        "client_message_id",
        "private_note",
        "private_note_in_dm",
        "written_in_lemma",
        "is_final_answer",
        "is_intermediate_assistant_message",
        "answer_from_last_words",
        "held_for_a_person",
        "folded_from",
        "synthetic_tool_return",
        "parked_in_run",
        # Written, never read: no longer written either.
        "interrupted_run_id",
        "replayed_on_resume",
        "replay_class",
        "reclassified_inline_reasoning",
        "author_user_id",
        "agent_run_id",
    }
)


def client_tags(metadata: Mapping[str, object] | None) -> JsonObject:
    """What a client sent as a new conversation's metadata, as it is kept."""
    return {
        key: value
        for key, value in (metadata or {}).items()
        if key not in SERVER_READ_KEYS
    }


def with_client_tags(
    existing: Mapping[str, object] | None, incoming: Mapping[str, object] | None
) -> JsonObject:
    """``incoming`` replacing a conversation's tags, the server's keys kept.

    A key the server reads is kept exactly as the server wrote it, whatever the
    client sent or left out: a surface binding names where a run's answer is
    posted, so a client that could rewrite it could aim the agent's answer at
    a thread of its choosing.
    """
    kept = {
        key: value for key, value in (existing or {}).items() if key in SERVER_READ_KEYS
    }
    return {**client_tags(incoming), **kept}


def surface_origin(
    *, contact_id: UUID | None, for_notification: bool
) -> ConversationOrigin:
    """What a conversation a chat surface opens is for: delivering a
    notification, answering someone outside the pod, or a member."""
    if for_notification:
        return ConversationOrigin.NOTIFICATION
    if contact_id is not None:
        return ConversationOrigin.CONTACT
    return ConversationOrigin.SURFACE
