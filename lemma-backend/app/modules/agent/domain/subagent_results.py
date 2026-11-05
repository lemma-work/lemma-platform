"""A sub-agent's result is owed to the conversation that started it.

A parent can collect a child's result three ways: park on ``wait_for``, block on
``interact_subagent(action='await')``, or read ``status``. A parent that did none
of them -- spawned the child and carried on, then ended its turn -- used to never
hear of the result at all. So a finished child's result is delivered into the
parent as a new message, once, unless the parent already collected it.

"Once" is a claim on the child *run*: the first of the collectors or the
delivery to reach a finished run marks it, and every other one finds it marked.
Delivery also holds off while the parent run that started the child is still
going, because that run may yet await the child itself; its own finish delivers
what it left uncollected.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import UUID

from app.modules.agent.domain.value_objects import AgentRunStatus, JsonValue

# The child conversation's `reports_to_parent` says its runs' results are owed
# to the parent: set at spawn, so children spawned before delivery existed --
# whose results nobody is waiting for any more -- are never delivered late. Its
# `parent_run_id`, set at spawn and on every ``send``, is the parent run that
# last gave it work, and so the one that may collect it. The parent's
# `delegations_out` says it has children whose results may be owed to it, so
# its own finished runs look for them. A child run's `delivered_to_parent_at`
# says its result has reached the parent, by whichever collector came first.

#: ``source`` on the message that carries a result into the parent.
RESULT_SOURCE = "subagent_result"

#: The run outcomes that are a result. A stopped child was stopped by its
#: parent or by a person, and either already knows.
DELIVERED_STATUSES = frozenset({AgentRunStatus.COMPLETED, AgentRunStatus.FAILED})

#: The longest result carried inline. The message is replayed on every later
#: turn of the parent, so a large output is cut and the rest left to
#: ``query_subagents``.
MAX_RESULT_CHARS = 20_000


def result_text(output: JsonValue) -> str:
    """The child's output as the parent will read it."""
    if isinstance(output, str):
        return output
    if isinstance(output, Mapping):
        answer = output.get("answer")
        if isinstance(answer, str):
            return answer
    if output is None:
        return ""
    return json.dumps(output, ensure_ascii=False, indent=2, default=str)


def result_message(
    *,
    agent_name: str,
    child_conversation_id: UUID,
    status: AgentRunStatus,
    output: JsonValue,
    error: str | None,
) -> str:
    """What the parent is told when a child it did not wait for finishes."""
    who = f"The sub-agent {agent_name} (conversation {child_conversation_id})"
    if status is AgentRunStatus.FAILED:
        return f"{who} failed: {error or 'no reason was recorded'}."
    text = result_text(output)
    if not text:
        return f"{who} finished without writing a result."
    if len(text) > MAX_RESULT_CHARS:
        text = (
            text[:MAX_RESULT_CHARS]
            + "\n\n[Cut here. Read the rest with query_subagents.]"
        )
    return f"{who} finished. Its result:\n\n{text}"


def result_metadata(
    *, child_conversation_id: UUID, child_run_id: UUID
) -> dict[str, object]:
    return {
        "source": RESULT_SOURCE,
        "subagent_conversation_id": str(child_conversation_id),
        "subagent_run_id": str(child_run_id),
    }
