"""Rebuild a pydantic-ai conversation from Lemma's persisted messages.

Kept apart from the harness because it is a pure transform with no runtime
state: given the message rows, it produces the ``ModelMessage`` history and the
user prompt for the next turn. Its one subtle rule is tool pairing — a tool call
without its matching return is dropped rather than sent, because a model
rejects a call it never saw answered.

Reasoning has a rule of its own, and it lives in `pydantic_ai_thinking`
beside the credential check it depends on.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

import pydantic_core
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.core.log.log import get_logger
from app.modules.agent.infrastructure.harnesses.pydantic_ai_thinking import (
    PendingThoughts,
)
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.pausing_tools import PAUSING_TOOL_NAMES
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.infrastructure.harnesses.user_turn_text import (
    timed_user_prompt_text,
)
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.domain.value_objects import (
    TEXTUAL_MESSAGE_KINDS,
    QueueState,
    MessageKind,
    MessageRole,
)

logger = get_logger(__name__)


def history_and_prompt(
    messages: Sequence[Message],
    *,
    protocol: RuntimeProfileProtocol | None = None,
    current_run_id: UUID | None = None,
    replay_prior_run_reasoning: bool = False,
) -> tuple[list[ModelMessage], str | None]:
    """``protocol`` names where the history is going, and gates thought replay.

    Optional, and defaulting to None on purpose: a caller that does not say
    replays no reasoning at all. That is the safe direction — the cost is a
    model that cannot see its own earlier thinking, and the alternative is
    reasoning written into an answer. ``current_run_id`` and
    ``replay_prior_run_reasoning`` say which of the thoughts that pass that
    test are this run's own; see `pydantic_ai_thinking`.
    """
    ordered = _without_unanswered_thoughts(
        sorted(messages, key=lambda message: message.sequence)
    )
    user_prompt: str | None = None
    history_messages = list(ordered)
    if (
        ordered
        and ordered[-1].role is MessageRole.USER
        and ordered[-1].kind in TEXTUAL_MESSAGE_KINDS
    ):
        user_prompt = timed_user_prompt_text(ordered[-1])
        history_messages = ordered[:-1]

    # The trailing user turn is carried as the prompt rather than in history,
    # so the builder cannot see it -- and it is exactly what tells an unanswered
    # question apart from one still being asked.
    thoughts = PendingThoughts(
        protocol,
        current_run_id=current_run_id,
        replay_prior_runs=replay_prior_run_reasoning,
    )
    return (
        _to_pydantic_ai_messages(
            history_messages, thoughts, user_turn_follows=user_prompt is not None
        ),
        user_prompt,
    )


def _without_unanswered_thoughts(ordered: list[Message]) -> list[Message]:
    """``ordered`` without the reasoning trailing after its last user turn.

    A run that failed after only thinking leaves its thoughts newest, and a
    retry of it replays the user turn before them. A thought with no answer
    after it is never sent to the model (``PendingThoughts`` drops it), so
    leaving them in only hid the turn the retry is for, and the run started
    with no prompt.
    """
    end = len(ordered)
    while (
        end
        and ordered[end - 1].role is MessageRole.ASSISTANT
        and ordered[end - 1].kind is MessageKind.THINKING
    ):
        end -= 1
    return ordered[:end]


def _to_pydantic_ai_messages(
    messages: Iterable[object],
    pending: PendingThoughts,
    *,
    user_turn_follows: bool = False,
) -> list[ModelMessage]:
    items = list(messages)
    converted: list[ModelMessage] = []
    consumed_tool_return_indexes: set[int] = set()
    index = 0

    while index < len(items):
        if index in consumed_tool_return_indexes:
            index += 1
            continue

        msg = items[index]
        role = _normalize_role(getattr(msg, "role", ""))
        kind = getattr(msg, "kind", None)

        if role == MessageRole.USER:
            pending.drop()
            converted.append(
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content=timed_user_prompt_text(msg, with_roster=False)
                        )
                    ]
                )
            )
            index += 1
            continue

        if role == MessageRole.SYSTEM:
            pending.drop()
            converted.append(
                ModelRequest(parts=[SystemPromptPart(content=_message_text(msg))])
            )
            index += 1
            continue

        if role == MessageRole.ASSISTANT:
            if kind in (MessageKind.TEXT, MessageKind.NOTIFICATION):
                converted.append(
                    ModelResponse(
                        parts=[
                            *pending.take(),
                            TextPart(content=_said_text(msg)),
                        ],
                        timestamp=getattr(msg, "created_at", None),
                    )
                )
                index += 1
                continue

            if kind == MessageKind.THINKING:
                pending.offer(msg)
                index += 1
                continue

            if kind == MessageKind.TOOL_CALL:
                (
                    response_message,
                    request_message,
                    next_index,
                    consumed_indexes,
                ) = _build_tool_batch(
                    items,
                    index,
                    consumed_tool_return_indexes,
                    user_turn_follows=user_turn_follows,
                )
                if response_message is not None:
                    response_message.parts = [
                        *pending.take(),
                        *response_message.parts,
                    ]
                    converted.append(response_message)
                else:
                    pending.drop()
                if request_message is not None:
                    converted.append(request_message)
                consumed_tool_return_indexes.update(consumed_indexes)
                index = next_index
                continue

        if role == MessageRole.TOOL:
            index += 1
            continue

        logger.debug("agent.pydantic_ai.skipping_unknown_agent_message_role.diagnostic")
        index += 1

    # A thought the run never followed with anything has nothing to ride on.
    pending.drop()
    return converted


def _collect_tool_calls(
    messages: list[object], start_index: int
) -> tuple[list[object], int]:
    """The run of consecutive assistant tool calls starting here.

    A model can ask for several tools in one response, and they have to be
    rebuilt into one `ModelResponse` -- splitting them would misrepresent the
    turn and break the tool_use/tool_result pairing Anthropic requires.
    """
    call_entries: list[object] = []
    index = start_index
    while index < len(messages):
        msg = messages[index]
        role = _normalize_role(getattr(msg, "role", ""))
        if (
            role != MessageRole.ASSISTANT
            or getattr(msg, "kind", None) != MessageKind.TOOL_CALL
        ):
            break
        call_entries.append(msg)
        index += 1
    return call_entries, index


def _match_tool_returns(
    messages: list[object],
    start_index: int,
    consumed_tool_return_indexes: set[int],
) -> dict[str, tuple[int, object]]:
    """The returns belonging to those calls, keyed by tool call id.

    The search stops at the next assistant message or the next user message,
    because a return after either of those belongs to a later turn. `setdefault`
    keeps the first return for an id: a resumed pause can leave two, and the
    earlier one is the one the model was shown.

    Except a user message sent while the call was still running: it is stored
    where it was typed, which is before the return the call wrote afterwards,
    but it did not end the call's turn. Stopping at it lost the return -- an
    approved action's result was replaced by "the user did not answer" -- and
    the loop that called this places the message after the returns anyway.
    """
    matched: dict[str, tuple[int, object]] = {}
    index = start_index
    while index < len(messages):
        msg = messages[index]
        role = _normalize_role(getattr(msg, "role", ""))
        kind = getattr(msg, "kind", None)
        if role == MessageRole.TOOL and kind == MessageKind.TOOL_RETURN:
            if index not in consumed_tool_return_indexes:
                matched.setdefault(getattr(msg, "tool_call_id", None), (index, msg))
            index += 1
            continue
        if role == MessageRole.USER and _sent_while_running(msg):
            index += 1
            continue
        if role in (MessageRole.ASSISTANT, MessageRole.USER):
            break
        index += 1
    return matched


def _sent_while_running(message: object) -> bool:
    state = getattr(message, "state", None)
    return isinstance(state, MessageState) and state.queue_state is not QueueState.NONE


#: Where a tool call's provider identity is stored on its message row. Some
#: endpoints attach data to a call that they require back on it, and a run that
#: resumes -- after an approval, say -- rebuilds the call from this row.
TOOL_CALL_PROVIDER_NAME_KEY = "tool_call_provider_name"
TOOL_CALL_PROVIDER_DETAILS_KEY = "tool_call_provider_details"


def tool_call_identity(part: object) -> dict[str, object]:
    """The metadata that lets a stored tool call be sent back as it arrived."""
    name = getattr(part, "provider_name", None)
    details = getattr(part, "provider_details", None)
    if not isinstance(name, str) or not name or not isinstance(details, dict):
        return {}
    return {
        TOOL_CALL_PROVIDER_NAME_KEY: name,
        TOOL_CALL_PROVIDER_DETAILS_KEY: details,
    }


def _stored_tool_call_identity(msg: object) -> dict[str, Any]:
    metadata = getattr(msg, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    name = metadata.get(TOOL_CALL_PROVIDER_NAME_KEY)
    details = metadata.get(TOOL_CALL_PROVIDER_DETAILS_KEY)
    if not isinstance(name, str) or not name or not isinstance(details, dict):
        return {}
    return {"provider_name": name, "provider_details": details}


def _user_moved_on(messages: list[object], after_index: int) -> bool:
    """Whether the person sent something new instead of answering.

    An unmatched `ask_user` normally means the conversation is still waiting on
    a human, and telling the model its question failed while the person is
    still being asked it would be a lie. But a user message *after* the
    question means they did not answer it -- they said something else, and that
    is the turn being responded to.
    """
    return any(
        getattr(message, "role", None) is MessageRole.USER
        for message in messages[after_index:]
    )


def _build_tool_batch(
    messages: list[object],
    start_index: int,
    consumed_tool_return_indexes: set[int],
    *,
    user_turn_follows: bool = False,
) -> tuple[ModelResponse | None, ModelRequest | None, int, set[int]]:
    call_entries, index = _collect_tool_calls(messages, start_index)
    matched_returns = _match_tool_returns(messages, index, consumed_tool_return_indexes)

    response_parts: list[ToolCallPart] = []
    request_parts: list[ToolReturnPart] = []
    consumed_indexes: set[int] = set()
    request_timestamp = None

    for msg in call_entries:
        matched = matched_returns.get(getattr(msg, "tool_call_id", None))
        parsed_args = parse_tool_call_args(getattr(msg, "tool_args", None))

        # A call with no result, or with arguments that never parsed, used to be
        # erased from the reconstructed history entirely. The agent then had no
        # memory of having tried, so it either re-issued the call blind or
        # reasoned as though it had never happened. Telling it what went wrong
        # is strictly more useful — and it keeps the tool_use/tool_result
        # pairing that Anthropic requires.
        #
        # Pausing tools are the exception: an unmatched `ask_user` /
        # `request_approval` / `wait_for` is not a failure, it is the marker that
        # the conversation is waiting on a human (see
        # `services/pause_resume.PAUSING_TOOL_NAMES`, and the pending-approval
        # detection that keys on exactly this shape). Synthesizing a failure
        # there would tell the model its question failed while the user is still
        # being asked it.
        synthetic_error: str | None = None
        if matched is None:
            if getattr(msg, "tool_name", None) in PAUSING_TOOL_NAMES:
                if not (user_turn_follows or _user_moved_on(messages, index)):
                    logger.debug(
                        "agent.pydantic_ai.skipping_tool_call_without_matching.diagnostic",
                        tool_call_id=msg.tool_call_id,
                    )
                    continue
                # They were asked and said something else. Dropping the call
                # here leaves no record of having asked, so the model asks the
                # same question again instead of reading what they actually
                # sent.
                synthetic_error = (
                    "The user did not answer this. They sent a new message "
                    "instead, which is the turn you are responding to now. "
                    "Read it and carry on; ask again only if you still cannot "
                    "proceed without an answer."
                )
            else:
                # Deliberately not "run it again". The tool may well have
                # succeeded -- what is known is only that no result was
                # recorded, and a run interrupted mid-send that is told to
                # retry sends twice.
                synthetic_error = (
                    "This tool call was interrupted before a result was "
                    "recorded, so its outcome is unknown: it may have completed. "
                    "Check the current state before repeating it, and only "
                    "repeat it if it clearly did not take effect."
                )
        elif parsed_args is None:
            synthetic_error = (
                "The arguments recorded for this call could not be parsed, so "
                "it never ran. Re-issue it with valid arguments."
            )

        return_index, return_msg = matched if matched is not None else (None, None)
        if return_index is not None:
            consumed_indexes.add(return_index)
        if request_timestamp is None and return_msg is not None:
            request_timestamp = getattr(return_msg, "created_at", None)

        response_parts.append(
            ToolCallPart(
                tool_name=msg.tool_name,
                tool_call_id=msg.tool_call_id,
                # Keep the raw arguments when they did not parse: the model can
                # see what it sent and correct it.
                args=parsed_args if parsed_args is not None else {},
                **_stored_tool_call_identity(msg),
            )
        )
        request_parts.append(
            ToolReturnPart(
                tool_name=getattr(return_msg, "tool_name", None) or msg.tool_name,
                tool_call_id=msg.tool_call_id,
                content=(
                    {"success": False, "error": synthetic_error}
                    if synthetic_error is not None
                    else getattr(return_msg, "tool_result", None)
                ),
            )
        )

    response_message = None
    request_message = None
    if response_parts:
        response_message = ModelResponse(
            parts=response_parts,
            timestamp=getattr(call_entries[0], "created_at", None),
        )
        request_message = ModelRequest(parts=request_parts, timestamp=request_timestamp)

    return response_message, request_message, index, consumed_indexes


def _normalize_role(role: object) -> MessageRole | None:
    value = role.value if hasattr(role, "value") else role
    try:
        return MessageRole(str(value))
    except ValueError:
        return None


def _message_text(msg: object) -> str:
    return getattr(msg, "text", None) or ""


#: How a member's own words, written into a conversation the agent answers,
#: reach the agent: on its side of the conversation, where whoever it answers
#: cannot write, and marked as a person's -- otherwise they read as something
#: the agent said itself, and an answer the pod gave is doubted as its own guess.
MEMBER_WROTE = "[A member of the pod wrote this to them directly, not you:]\n"


def _said_text(msg: object) -> str:
    """An assistant-side line's text, marked when a member wrote it."""
    text = _message_text(msg)
    metadata = getattr(msg, "metadata", None) or {}
    if isinstance(metadata, dict) and metadata.get("follow_up"):
        return MEMBER_WROTE + text
    return text


def parse_tool_call_args(args: object) -> dict[str, object] | None:
    """Return tool args as a JSON object or ``None`` if malformed."""

    if not args:
        return {}

    if isinstance(args, dict):
        return args

    if not isinstance(args, str):
        logger.debug("agent.pydantic_ai.dropping_non_object_tool_args.diagnostic")
        return None

    try:
        parsed = pydantic_core.from_json(args)
    except ValueError:
        logger.debug("agent.pydantic_ai.ignoring_malformed_tool_args_json.diagnostic")
        return None

    if isinstance(parsed, dict):
        return parsed

    logger.debug("agent.pydantic_ai.ignoring_tool_args_that_did.diagnostic")
    return None
