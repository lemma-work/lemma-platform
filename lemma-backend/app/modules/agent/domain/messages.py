"""A message, before it has a row: its role, its kind, and the draft of one.

Split out of ``value_objects`` when that file reached the architecture
ratchet's per-file limit. Everything here is still re-exported from there, so
no import site had to move, and both names go on meaning the same thing.

These four travel together: ``MessageDraft`` is built from ``MessageRole`` and
``MessageKind`` by every ``of_*`` constructor, and ``TEXTUAL_MESSAGE_KINDS``
is the set of kinds whose body is in ``text``.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel

JsonValue = object
JsonObject = dict[str, object]


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessageKind(str, Enum):
    """Discriminates the flat message body.

    A message carries exactly one kind. Textual kinds use ``text``; tool kinds
    use ``tool_name``/``tool_call_id`` plus ``tool_args`` (call) or
    ``tool_result`` (return). There is no nested ``content`` object.
    """

    TEXT = "TEXT"
    NOTIFICATION = "NOTIFICATION"
    THINKING = "THINKING"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RETURN = "TOOL_RETURN"

    @classmethod
    def _missing_(cls, value: object) -> "MessageKind | None":
        # Case-insensitive so a lowercase kind from a harness payload or a
        # pre-migration row still resolves to the CAPS member.
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper()
        for member in cls:
            if member.value == normalized:
                return member
        return None


#: What a run produced on the way to an answer, as opposed to the answer. In a
#: conversation with more than one person in it these belong to whoever
#: triggered the run: tool arguments carry the question they asked and tool
#: results carry data their grants reached, neither of which the rest of the
#: room is owed. The final text is the conversation's.
WORKING_MESSAGE_KINDS = frozenset(
    {MessageKind.THINKING, MessageKind.TOOL_CALL, MessageKind.TOOL_RETURN}
)

TEXTUAL_MESSAGE_KINDS = frozenset(
    {MessageKind.TEXT, MessageKind.NOTIFICATION, MessageKind.THINKING}
)


class MessageDraft(BaseModel):
    """Harness-produced message before durable DB id/sequence assignment.

    Flat by construction: the body lives directly on the draft instead of a
    nested ``content`` union, so there is no ``content.content`` indirection.
    Build via the ``of_*`` constructors to keep call sites readable.
    """

    role: MessageRole
    kind: MessageKind
    #: Set only on messages a person wrote. Carried on the draft rather than
    #: derived from `role` at write time, because the writer is the only layer
    #: that knows *which* person the request came from.
    sender_user_id: UUID | None = None
    text: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_args: JsonValue = None
    tool_result: JsonValue = None
    metadata: JsonObject | None = None

    @classmethod
    def of_text(
        cls,
        text: str,
        *,
        role: MessageRole = MessageRole.ASSISTANT,
        sender_user_id: UUID | None = None,
        metadata: JsonObject | None = None,
    ) -> "MessageDraft":
        return cls(
            role=role,
            kind=MessageKind.TEXT,
            text=text,
            sender_user_id=sender_user_id,
            metadata=metadata,
        )

    @classmethod
    def of_thinking(
        cls,
        text: str,
        *,
        role: MessageRole = MessageRole.ASSISTANT,
        metadata: JsonObject | None = None,
    ) -> "MessageDraft":
        return cls(role=role, kind=MessageKind.THINKING, text=text, metadata=metadata)

    @classmethod
    def of_notification(
        cls,
        text: str,
        *,
        role: MessageRole = MessageRole.ASSISTANT,
        metadata: JsonObject | None = None,
    ) -> "MessageDraft":
        return cls(
            role=role, kind=MessageKind.NOTIFICATION, text=text, metadata=metadata
        )

    @classmethod
    def of_tool_call(
        cls,
        *,
        tool_name: str,
        tool_call_id: str,
        tool_args: JsonValue = None,
        role: MessageRole = MessageRole.ASSISTANT,
        metadata: JsonObject | None = None,
    ) -> "MessageDraft":
        return cls(
            role=role,
            kind=MessageKind.TOOL_CALL,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_args=tool_args,
            metadata=metadata,
        )

    @classmethod
    def of_tool_return(
        cls,
        *,
        tool_call_id: str,
        tool_name: str | None = None,
        tool_result: JsonValue = None,
        role: MessageRole = MessageRole.TOOL,
        metadata: JsonObject | None = None,
    ) -> "MessageDraft":
        return cls(
            role=role,
            kind=MessageKind.TOOL_RETURN,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_result=tool_result,
            metadata=metadata,
        )
