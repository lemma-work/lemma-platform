"""Resolved route and destination, shared by the ingress mixins.

These two were defined in :mod:`ingress_service`, which is where they were used
from until the service was split. They cannot stay there: every mixin that needs
one is imported *by* that module, so reaching back for the type would be a
cycle. A leaf module both sides can import is the way out.

`SurfaceEgressTarget` loses its leading underscore in the move -- it is named
across modules now, and a private name that half the package imports is only
private by spelling.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

from dataclasses import dataclass, replace

from app.modules.agent_surfaces.domain.reply_targets import copied_on_reply
from uuid import UUID

from app.modules.agent.contracts.audience import Audience

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.channels.contracts import Source, SourceKind


@dataclass(frozen=True)
class ResolvedSurfaceRoute:
    """Who answers, and in which pod -- the destination, not the transport.

    `pod_id` is here rather than read off the surface because the two are not
    always the same thing. A personal DM arrives through a company's
    installation and is answered by the person's own pod, and a surface that
    had to pretend otherwise is how pod data ended up being read from the
    wrong place.
    """

    pod_id: UUID
    agent_id: UUID | None
    agent_name: str | None
    agent_display_name: str
    conversation_kind: str
    route_key: str


@dataclass(frozen=True)
class SurfaceEgressTarget:
    """Resolved destination for an outbound surface message.

    `source` is the place the reply goes to, and `event` what answers it there
    -- the line being answered, or the place's last word. `surface` is the
    installation the reply goes out through; `pod_id` is the pod the
    conversation belongs to. For a personal DM those differ, so anything
    reading pod data -- a file, a table, a deep link -- must use `pod_id` and
    not `surface.pod_id`.
    """

    source: Source
    conversation_id: UUID
    #: The agent the conversation is with: whose name the reply goes out under.
    agent_id: UUID | None
    surface: AgentSurfaceEntity
    pod_id: UUID
    adapter: SurfacePlatformAdapterPort
    event: ParsedInboundSurfaceEvent
    credentials: SurfaceCredentials
    #: Whose conversation this is: the member a group answer was made for.
    conversation_user_id: UUID | None = None
    #: Whom the conversation answers: that member, or a contact they look after.
    audience: Audience = Audience()

    @property
    def is_group(self) -> bool:
        """Several people read what goes out here: a group, a shared channel."""
        return self.source.kind is SourceKind.CHAT

    @property
    def person_external_id(self) -> str | None:
        """The platform id of the person this answers: the private place's
        person, or whoever said what is being answered in a group."""
        if self.source.kind is SourceKind.PERSON and self.source.external_user_id:
            return self.source.external_user_id
        # As identity resolution names them: the directory id where the
        # platform gives one (Teams' AAD object id), else its own.
        return self.event.sender_aad_object_id or self.event.sender_external_user_id

    @property
    def copies_others(self) -> bool:
        """Whether what goes out here also reaches people besides the one it
        answers -- an email thread's reply-all, which copies everybody on it."""
        return bool(copied_on_reply(self.event))

    def to_sender_only(self) -> SurfaceEgressTarget:
        """The same thread, reaching only the person it answers."""
        reply_target = {
            key: value for key, value in self.event.reply_target.items() if key != "cc"
        }
        return replace(
            self, event=self.event.model_copy(update={"reply_target": reply_target})
        )
