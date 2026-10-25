"""The ``metadata`` a client is shown: the stored JSON, plus the typed state
under the keys it used to be read from.

The web app, the SDKs and the CLI read a conversation's origin and a message's
queue state, answer flags and send id off ``metadata`` -- they were keys there
until they became columns (``domain/conversations``). The response keeps
saying them in those words, built from the columns, until the clients read
typed fields; the server itself never reads them back from here.
"""

from __future__ import annotations

from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.value_objects import (
    AnswerKind,
    ConversationOrigin,
    JsonObject,
    QueueState,
)

#: The ``source`` a conversation of each origin was opened with.
_LEGACY_SOURCES: dict[ConversationOrigin, str] = {
    ConversationOrigin.SURFACE: "agent_surfaces",
    ConversationOrigin.CHANNEL: "lemma_channel",
    ConversationOrigin.WORKFLOW_STEP: "WORKFLOW_RUN",
    ConversationOrigin.SCHEDULE: "SCHEDULE",
    ConversationOrigin.POD_ASK: "pod_ask",
    ConversationOrigin.NOTIFICATION: "notification",
}


def _legacy_source(conversation: Conversation) -> str | None:
    if conversation.origin is ConversationOrigin.CONTACT:
        stored = conversation.metadata or {}
        if stored.get("web_widget_id"):
            return "web_widget"
        if stored.get("surface_platform"):
            return "agent_surfaces"
        return "lemma_channel" if conversation.channel_id else None
    return _LEGACY_SOURCES.get(conversation.origin)


def conversation_wire_metadata(conversation: Conversation) -> JsonObject | None:
    """``conversation``'s metadata as a client reads it."""
    wire: JsonObject = dict(conversation.metadata or {})
    typed: JsonObject = {
        "source": _legacy_source(conversation),
        "workflow_run_id": (
            str(conversation.workflow_run_id) if conversation.workflow_run_id else None
        ),
        "started_by": (
            "SCHEDULE" if conversation.origin is ConversationOrigin.SCHEDULE else None
        ),
        "is_sub_agent": (
            True if conversation.origin is ConversationOrigin.SUBAGENT else None
        ),
        "ask": conversation.ask.to_json() if conversation.ask else None,
        "call": conversation.call.to_json() if conversation.call else None,
        "cwd": conversation.cwd,
        "workspace_id": conversation.workspace_id,
        "workspace_name": conversation.workspace_name,
        "repo": conversation.repo.to_json() if conversation.repo else None,
        "todos": [todo.to_json() for todo in conversation.todos] or None,
    }
    wire.update({key: value for key, value in typed.items() if value is not None})
    return wire if wire or conversation.metadata is not None else None


#: The answer flags each kind was stored as.
_ANSWER_FLAGS: dict[AnswerKind, JsonObject] = {
    AnswerKind.FINAL: {"is_final_answer": True},
    AnswerKind.LAST_WORDS: {"is_final_answer": True, "answer_from_last_words": True},
    AnswerKind.INTERMEDIATE: {
        "is_final_answer": False,
        "is_intermediate_assistant_message": True,
    },
    AnswerKind.NOT_FINAL: {"is_final_answer": False},
}


def _queue_keys(message: Message) -> JsonObject:
    """The queue flags ``message``'s state was stored as."""
    state = message.state.queue_state
    if state is QueueState.NONE:
        return {}
    keys: JsonObject = {"during_active_run": True}
    if state is QueueState.STEERED:
        run = message.state.steered_into_run_id
        keys["steered_into_run"] = str(run) if run else True
    elif state is QueueState.STEER_UNDELIVERED:
        keys["steer_undelivered"] = "undelivered"
    elif state is QueueState.WAITING_ON_ANSWER:
        # Held behind the question of the run it was queued behind.
        keys["waiting_on_answer"] = (
            str(message.agent_run_id) if message.agent_run_id else True
        )
    return keys


def message_wire_metadata(message: Message) -> JsonObject:
    """``message``'s metadata as a client reads it."""
    state = message.state
    typed: JsonObject = {
        "steer_dispatched_at": (
            state.steer_dispatched_at.isoformat() if state.steer_dispatched_at else None
        ),
        "client_message_id": state.client_message_id,
        "private_note": state.private_note or None,
        "private_note_in_dm": state.private_note_in_dm or None,
        "written_in_lemma": state.written_in.value.lower()
        if state.written_in
        else None,
        "held_for_a_person": state.held_for_a_person or None,
        "folded_from": str(state.folded_from) if state.folded_from else None,
        "synthetic_tool_return": state.synthetic_tool_return or None,
        "parked_in_run": True if state.parked_in_run_id else None,
    }
    return {
        **(message.metadata or {}),
        **_queue_keys(message),
        **(_ANSWER_FLAGS[state.answer] if state.answer else {}),
        **{key: value for key, value in typed.items() if value is not None},
    }
