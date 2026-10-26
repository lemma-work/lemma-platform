"""Which keys of a conversation's metadata left it.

A conversation's metadata is its client's tags: the web app writes
``lemma_resource`` and ``lemma_attached_file`` there, an SDK user any key of
their own, and the list filter ``?metadata=`` reads them back. Everything the
server decides on is a typed column of the row, which no request reaches
(``Conversation`` documents each), or agent_surfaces' own record of where a
chat surface heard it.

``MOVED_*_KEYS`` were metadata once and are columns, or another table, now. A
client may still send one -- it is a tag like any other, and the list filter
finds it -- but it changes nothing, because the column is what is read. These
lists are also what ``tests/unit/test_moved_metadata_keys`` holds every
``*_metadata`` read and write in ``app/`` against.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.value_objects import ConversationOrigin

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
        "schedule_type",
        # Where a chat surface heard it, and what agent_surfaces keeps against
        # it: agent_surfaces' `surface_conversation_state`.
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
        "agent_display_name",
        "web_widget_id",
        "web_session_id",
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
