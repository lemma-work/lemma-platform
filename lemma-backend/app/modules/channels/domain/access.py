"""Who may see a channel. One rule; every read path asks it.

* **DIRECT**: its member only.
* **CONTACT**: members who may see the pod's contacts (``contact.read``).
* **GROUP**, ``public``: every member of the pod.
* **GROUP**, ``private``: its members only. A mirrored private chat (a Slack
  private channel, a group DM, a Teams group chat, a WhatsApp or Telegram
  group) is private; so is a group a member made private. An admin joins one
  to read it, like anybody else -- there is no reading over the wall.

The same rule is a SQL predicate (``infrastructure.access``) for lists,
counts, inbox and search, and this function for one channel in hand. The two
are tested against each other over every kind of channel.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.channels.domain.entities import Channel, ChannelKind, Visibility


class Viewer(BaseModel):
    """A member of the pod looking at its channels, with what decides it."""

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    pod_id: UUID
    #: Holds ``contact.read`` in the pod.
    sees_contacts: bool = False


def may_see(channel: Channel, viewer: Viewer, *, is_member: bool) -> bool:
    """Whether ``viewer`` may see ``channel``. ``is_member``: whether they are
    one of its members, which only a private group asks."""
    if channel.pod_id != viewer.pod_id:
        return False
    match channel.kind:
        case ChannelKind.DIRECT:
            return channel.member_user_id == viewer.user_id
        case ChannelKind.CONTACT:
            return viewer.sees_contacts
        case ChannelKind.GROUP:
            return channel.visibility is Visibility.PUBLIC or is_member
