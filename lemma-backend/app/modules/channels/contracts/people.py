"""How every API names a person: a member, a contact, or an agent."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.channels.domain.entities import SenderKind

__all__ = ["PersonRef", "SenderKind"]


class PersonRef(BaseModel):
    """Somebody in a channel, said one way everywhere: a member of the pod, one
    of its contacts, or one of its agents. There is nobody else -- everybody
    outside the pod is a contact."""

    kind: SenderKind
    id: UUID | None = Field(
        default=None,
        description=(
            "The member's user id, the contact's id or the agent's id; absent "
            "for a member or an agent since removed."
        ),
    )
    name: str | None = Field(
        default=None, description="What to call them, where known."
    )
