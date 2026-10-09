"""Whom a bot answers beyond the pod's members, as the API reads and writes it.

Split from ``schemas`` because these two settings are one question -- the
people outside the pod, in groups and in private chats -- and are mirrored
across request and response.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent_surfaces.domain.surface_config import ContactAnswer


class SurfaceGroupsConfig(BaseModel):
    """How the bot treats people outside the pod in its groups. Mirrored."""

    answers_outsiders: bool = Field(
        default=True,
        description=(
            "Answer people outside the pod in this bot's groups, from what the "
            "pod made Public. Off, the bot answers only the pod's members, "
            "whatever a group's own switch says."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class SurfaceContactsConfig(BaseModel):
    """Whom the bot answers in private chats beyond the pod's members. Mirrored."""

    answer: ContactAnswer = Field(
        default=ContactAnswer.OFF,
        description=(
            "`off`: members only. `known`: members and the pod's existing "
            "contacts. `anyone`: a stranger becomes a contact with their first "
            "message. Only a bot that is the pod's own (its own token, number "
            "or email address) answers contacts."
        ),
    )
    looked_after_by: UUID | None = Field(
        default=None,
        description=(
            "The member contacts' conversations belong to. Defaults to whoever "
            "turns contacts on; must be a member of the pod."
        ),
    )

    model_config = ConfigDict(extra="forbid")
