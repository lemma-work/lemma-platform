"""Telling an agent that somebody is waiting on the person it is talking to.

When a notification is delivered, the message lands in the recipient's own
thread. Their next message is very often the answer — but the agent handling it
has no idea a question is outstanding, and no idea what the asker wanted done
with the reply. Without this capability the reply is just chat: nothing comes
back, and the asking run waits for something that will never arrive.

So this injects, at run assembly, the open asks on this conversation: what was
sent, and the ``background_instruction`` the asker wrote — which the recipient
never sees, and which is the only place the asker can say "record their status
update" or "write the PO number into purchase_orders".

Instructions, not a tool call, because the agent has to know *before* it decides
how to reply. A tool it must remember to call first is a tool it will sometimes
not call, and the failure is silent.
"""

from __future__ import annotations

from uuid import UUID

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset

from app.modules.agent.tools.messaging.respond import respond_toolset


def render_form_fields(schema: object) -> list[str]:
    """The form's actual fields, so the agent knows what it is listening for.

    Without this the agent is told a form is owed and given only a run id and a
    node id — it has to collect "structured values" whose shape it cannot see.
    The resolved schema was already stored on the notification when the
    assignment was created; it just never reached the prompt.

    Names, types, enums and requiredness only — no descriptions or defaults,
    which are usually prose and would crowd out the conversation itself.
    """
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return []
    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()

    lines = ["- Fields to collect:"]
    for name, spec in properties.items():
        spec = spec if isinstance(spec, dict) else {}
        parts = [str(spec.get("type") or "any")]
        choices = spec.get("enum")
        if isinstance(choices, list) and choices:
            parts.append("one of " + ", ".join(f"`{c}`" for c in choices[:10]))
        if name in required_names:
            parts.append("required")
        lines.append(f"  - `{name}` ({'; '.join(parts)})")
    return lines


def render_open_notifications(notifications: list[dict]) -> str:
    """Build the prompt fragment. Empty string when nothing is open.

    Every line is a separate list element rather than adjacent string literals:
    inside a list, implicit concatenation reads as a missing comma and is one
    typo away from silently merging two bullets.
    """
    if not notifications:
        return ""

    lines = [
        "## Open requests for this person",
        "",
        "Someone in this pod has asked them for something. Their next message",
        "may be the answer.",
        "",
    ]
    for item in notifications:
        lines.append(f"### {item['title']}")
        lines.append(f"- Request id: `{item['notification_id']}`")
        lines.append(f"- Sent to them: {item['body']}")
        if item.get("background_instruction"):
            lines.append(f"- Do with their reply: {item['background_instruction']}")
        if item.get("responds_through_action"):
            lines.append(
                "- Answered with `submit_workflow_form`, not a free-text response."
            )
            lines.extend(render_form_fields((item.get("action") or {}).get("schema")))
        lines.append("")

    lines.append(
        "Call `respond_to_notification` once you have a real answer — not when"
    )
    lines.append(
        "they say they will get to it. If they decline, leave it open and say"
    )
    lines.append("who was asking. Don't raise these while they're mid-topic.")
    return "\n".join(lines)


class OpenNotificationsCapability(AbstractCapability[object]):
    """Standing awareness of what this person owes, plus the tool to close it."""

    def __init__(self, instructions: str) -> None:
        self._instructions = instructions

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return "open_notifications"

    def get_toolset(self) -> AbstractToolset[object]:
        return respond_toolset

    def get_instructions(self) -> str:
        return self._instructions


async def build_open_notifications_capability(
    conversation_id: UUID,
) -> OpenNotificationsCapability | None:
    """None when nothing is open — the common case, and it must cost nothing.

    Deliberately not cached in the prompt prefix: unlike the platform fragment,
    this changes the moment somebody answers, and a stale copy would have an
    agent chasing a question that is already closed.
    """
    from app.composition.agent_notifications import (
        open_notifications_for_conversation,
    )

    # ``open_notifications_for_conversation`` already degrades to [] when the
    # lookup fails — a conversation must keep working, and the cost of a failed
    # read is a missed answer rather than a broken reply.
    notifications = await open_notifications_for_conversation(conversation_id)
    instructions = render_open_notifications(notifications)
    return OpenNotificationsCapability(instructions) if instructions else None
