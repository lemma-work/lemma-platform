"""The agent's "please sign in to this site" pause, as a person is asked about it.

The pause is the agent's (the browser tool persists ``origin`` and ``reason`` in
its call args, and the sign-in page is keyed on the conversation and tool call).
So what those args mean, where the link goes and what the message says are
written here; a surface only delivers the result on whatever channel the person
is on.

Pure functions, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SignInRequest:
    origin: str
    reason: str


def sign_in_request_from_tool_args(tool_args: object) -> SignInRequest | None:
    """The site and reason a persisted sign-in pause names, or ``None`` without a site."""
    if not isinstance(tool_args, dict):
        return None
    origin = str(tool_args.get("origin") or "")
    if not origin:
        return None
    return SignInRequest(origin=origin, reason=str(tool_args.get("reason") or ""))


def sign_in_link(frontend_url: str, *, conversation_id: UUID, tool_call_id: str) -> str:
    """Addressed by the pause it is for: the conversation and the tool call waiting.

    It used to be a row id, which meant a second record of what this link is
    about, kept in step by hand.
    """
    return (
        f"{frontend_url.rstrip('/')}/sign-in-to-site/{conversation_id}/{tool_call_id}"
    )


def sign_in_message(*, origin: str, reason: str, link: str) -> str:
    """The message body that sends somebody to sign in.

    ``reason`` is written by the model, so the caller passes it already made safe
    to show (a surface knows what its platform must not render).
    """
    lines = [f"I need you to sign in to {origin} so I can carry on."]
    if reason:
        lines.append(f"What I am doing: {reason}")
    lines.append(link)
    lines.append(
        "The link opens the site in my browser for you. I will not ask for your "
        "password and I cannot see it."
    )
    return "\n".join(lines)
