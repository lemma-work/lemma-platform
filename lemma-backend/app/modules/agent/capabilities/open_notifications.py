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


def render_open_notifications(notifications: list[dict]) -> str:
    """Build the prompt fragment. Empty string when nothing is open."""
    if not notifications:
        return ""

    lines = [
        "## Open requests for this person",
        "",
        "Someone in this pod has asked this person for something, and the "
        "request is still open. Their next message may well be the answer.",
        "",
    ]
    for item in notifications:
        lines.append(f"### {item['title']}")
        lines.append(f"- Request id: `{item['notification_id']}`")
        lines.append(f"- What they were sent: {item['body']}")
        if item.get("background_instruction"):
            lines.append(
                f"- What to do with their reply: {item['background_instruction']}"
            )
        if item.get("responds_through_action"):
            action = item.get("action") or {}
            lines.append(
                "- This one is answered by submitting a workflow form "
                f"(run `{action.get('run_id')}`, node `{action.get('node_id')}`), "
                "not by recording a free-text response."
            )
        lines.append("")

    lines.extend(
        [
            "When you have a real answer, call `respond_to_notification` with the "
            "request id and what they actually said. Only then — not when they "
            "say they will get to it.",
            "",
            "Do not raise these unprompted if they are talking about something "
            "else; answer what they asked, then bring it up. If they decline, "
            "leave the request open and tell them who was asking.",
        ]
    )
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
