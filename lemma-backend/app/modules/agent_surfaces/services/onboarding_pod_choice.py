"""Ask a recognised sender which workspace this conversation should use.

Someone who already has an account but no personal route is in an awkward
place: identity recognition knows exactly who they are, and there is still
nowhere for the conversation to go. Falling through to ordinary ingestion told
them to open the website and configure a surface, which is the one thing a
chat-first product should never have to say.

Provisioning silently instead would be worse in a different way -- a single
message would create a pod, and possibly an organization membership, for
someone who never asked for either. So this asks. It offers the workspaces they
already have, newest first, plus the option to name a new one, and only acts on
the answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.contracts.organizations import (
    preferred_organization_membership,
)
from app.modules.pod.contracts.user_pods import list_attachable_pods

if TYPE_CHECKING:
    from pydantic import JsonValue

#: How many workspaces to offer. Long enough to cover the ones a person
#: actually uses, short enough to read on a phone without scrolling.
MAX_OFFERED_PODS = 5

#: What to type to get a new one. Matched case-insensitively on the first word.
NEW_POD_KEYWORD = "new"


async def candidate_pods(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID, limit: int | None = MAX_OFFERED_PODS
) -> list[dict[str, JsonValue]]:
    """The workspaces this person could attach the conversation to.

    Shaped as plain JSON because it is stored on the pending row and read back
    when the answer arrives -- what was offered has to survive a restart, and a
    dataclass does not. `limit=None` asks for all of them, which is how the
    answer is re-checked against live access rather than against the handful
    that happened to be shown.
    """
    pods = await list_attachable_pods(session=uow.session, user_id=user_id, limit=limit)
    return [{"id": str(pod.id), "name": pod.name} for pod in pods]


def offer_text(pods: list[dict[str, JsonValue]]) -> str:
    """The question, as the person reads it."""
    if not pods:
        return (
            "You already have a Lemma account, but no workspace this chat can "
            f"use yet. Reply `{NEW_POD_KEYWORD} <name>` and I will make one -- "
            f"for example `{NEW_POD_KEYWORD} Personal`."
        )
    listed = "\n".join(
        f"{index}. {pod['name']}" for index, pod in enumerate(pods, start=1)
    )
    return (
        "You already have a Lemma account. Which workspace should this chat "
        f"use?\n\n{listed}\n\nReply with a number, or "
        f"`{NEW_POD_KEYWORD} <name>` to start a new one."
    )


class PodChoice:
    """What the reply asked for: an existing workspace, or a new one."""

    __slots__ = ("pod_id", "new_name")

    def __init__(
        self, *, pod_id: UUID | None = None, new_name: str | None = None
    ) -> None:
        self.pod_id = pod_id
        self.new_name = new_name


def read_choice(
    reply: str, offered: list[dict[str, JsonValue]] | None
) -> PodChoice | None:
    """Read the reply against the list that was actually shown.

    Returns None when it says neither -- the caller re-asks rather than
    guessing, because every wrong guess here wires a conversation to the wrong
    workspace.
    """
    text = (reply or "").strip()
    if not text:
        return None
    first, _, rest = text.partition(" ")
    if first.lower() == NEW_POD_KEYWORD:
        name = rest.strip()
        return PodChoice(new_name=name) if name else None
    if text.isdigit():
        index = int(text)
        pods = offered or []
        if 1 <= index <= len(pods):
            return PodChoice(pod_id=UUID(str(pods[index - 1]["id"])))
    return None


async def organization_for_new_pod(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    installation_organization_id: UUID | None,
) -> tuple[UUID, UUID] | None:
    """Where a newly named workspace should live: (organization, membership)."""
    return await preferred_organization_membership(
        uow,
        user_id=user_id,
        preferred_organization_id=installation_organization_id,
    )
