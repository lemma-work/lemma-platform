"""Where a conversation is answered on a chat platform, as this module records it.

A surface opens a conversation for a thread, or hears an existing one in a new
place, and writes down the place: which installation, which platform, the
platform's own channel, thread, user and message ids, and what routing decided.
A web widget writes down its visitor instead. The agent module never stores
any of it: it asks (``contracts/conversations``) when a run needs the platform
for its prompt or its tools, and its API shows clients the same facts under
the metadata keys they were read from before they were this module's.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from uuid import UUID

#: The fields an inbound message or a notification writes; a web visitor's are
#: written apart, so hearing a thread never forgets the widget it opened on.
PLACE_FIELDS = (
    "surface_id",
    "surface_platform",
    "conversation_kind",
    "external_channel_id",
    "external_thread_id",
    "external_user_id",
    "external_message_id",
    "channel_name",
    "route_key",
    "routed_agent_id",
    "agent_display_name",
    "surface_event_metadata",
)


@dataclass(frozen=True, slots=True)
class ConversationSurface:
    """The place a conversation was last written to from, or its web visitor."""

    surface_id: UUID | None = None
    #: ``SurfacePlatform`` value, uppercase.
    surface_platform: str | None = None
    #: ``DM``, ``CHANNEL`` or ``EMAIL``, as routing recorded it.
    conversation_kind: str | None = None
    external_channel_id: str | None = None
    external_thread_id: str | None = None
    external_user_id: str | None = None
    external_message_id: str | None = None
    channel_name: str | None = None
    route_key: str | None = None
    routed_agent_id: UUID | None = None
    #: The answering agent's name as the platform shows it.
    agent_display_name: str | None = None
    #: The platform's typed event metadata, as JSON (``surface_event_metadata``).
    surface_event_metadata: dict[str, object] | None = None
    web_widget_id: UUID | None = None
    web_session_id: UUID | None = None

    @property
    def is_web_widget(self) -> bool:
        return self.web_widget_id is not None

    def place(self) -> dict[str, object]:
        """The columns an inbound message or a notification writes."""
        return {name: getattr(self, name) for name in PLACE_FIELDS}

    def wire(self) -> dict[str, object]:
        """The metadata keys a client reads these under, set ones only.

        Ids as strings, as they were stored when this lived in the
        conversation's metadata.
        """
        return {
            key: str(value) if isinstance(value, UUID) else value
            for key, value in asdict(self).items()
            if value is not None
        }


__all__ = ["PLACE_FIELDS", "ConversationSurface"]
