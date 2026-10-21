"""Whether a platform keeps a place to its members, as the platform itself says.

A mirrored place's channel is private in Lemma when the platform keeps the
place to its members, and the safe answer to "we do not know" is *private*: a
private chat mirrored as public shows its talk to the whole pod, while a public
one mirrored as private only asks a member to join. So a place is public only
on the platform's word:

* **Slack** names the type on a ``message`` event (``channel`` is public;
  ``group``, ``mpim`` and ``im`` are not) but not on an ``app_mention``, which
  is often the first event a channel sends. ``conversations.info`` answers
  then (``settled_privacy``).
* **Teams** says nothing about a team channel's membership on a message. Graph
  does (``membershipType``: ``standard``, ``private`` or ``shared``); a group
  chat is always its members'.
* Every other platform's groups (WhatsApp, Telegram, an email thread with
  others on it) are their members' alone.

Once a place is known, the platform can only make it *more* private here: a
Slack channel converted to private flips its mirror, and nothing flips one
back without somebody in the pod choosing to.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.modules.agent_surfaces.domain.entities import SurfacePlatform


class PlacePrivacy(StrEnum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"


@dataclass(frozen=True, slots=True)
class PlaceFacts:
    """What a platform's own lookup says about one place."""

    privacy: PlacePrivacy
    #: The platform's name for the kind of place, where the event left it out
    #: (an ``app_mention`` names none): ``channel``, ``group``, ``mpim``.
    channel_type: str | None = None
    #: Shared with another company (Slack Connect).
    shared_externally: bool = False

    def onto(self, metadata: Mapping[str, object]) -> dict[str, object]:
        """``metadata`` with these facts written in, the event's own kept."""
        merged: dict[str, object] = {
            **metadata,
            PLACE_PRIVACY_KEY: self.privacy.value,
        }
        if self.channel_type and not metadata.get("channel_type"):
            merged["channel_type"] = self.channel_type
        if self.shared_externally:
            merged["is_ext_shared_channel"] = True
        return merged


#: Event metadata carrying the platform's own answer, looked up before a place
#: the event names is made (``services/place_privacy``).
PLACE_PRIVACY_KEY = "place_privacy"
#: Teams' ``membershipType`` for a team channel, where an event or Graph says it.
CHANNEL_MEMBERSHIP_KEY = "channel_membership_type"

_SLACK_PUBLIC = frozenset({"channel"})
_SLACK_PRIVATE = frozenset({"group", "mpim", "im"})
_TEAMS_PRIVATE_CHATS = frozenset({"groupchat", "personal"})
_TEAMS_PRIVATE_MEMBERSHIPS = frozenset({"private", "shared"})


def said_privacy(
    platform: SurfacePlatform, metadata: Mapping[str, object]
) -> PlacePrivacy | None:
    """What the platform said about a place's privacy, or None if it said nothing."""
    settled = metadata.get(PLACE_PRIVACY_KEY)
    if settled in (PlacePrivacy.PUBLIC.value, PlacePrivacy.PRIVATE.value):
        return PlacePrivacy(str(settled))
    kind = str(metadata.get("channel_type") or "").strip().lower()
    if platform is SurfacePlatform.SLACK:
        if kind in _SLACK_PUBLIC:
            return PlacePrivacy.PUBLIC
        return PlacePrivacy.PRIVATE if kind in _SLACK_PRIVATE else None
    if platform is SurfacePlatform.TEAMS:
        if kind in _TEAMS_PRIVATE_CHATS:
            return PlacePrivacy.PRIVATE
        membership = str(metadata.get(CHANNEL_MEMBERSHIP_KEY) or "").strip().lower()
        if membership == "standard":
            return PlacePrivacy.PUBLIC
        return (
            PlacePrivacy.PRIVATE if membership in _TEAMS_PRIVATE_MEMBERSHIPS else None
        )
    return PlacePrivacy.PRIVATE


def is_private_place(platform: SurfacePlatform, metadata: Mapping[str, object]) -> bool:
    """Private unless the platform said public: unknown fails closed."""
    return said_privacy(platform, metadata) is not PlacePrivacy.PUBLIC


__all__ = [
    "CHANNEL_MEMBERSHIP_KEY",
    "PLACE_PRIVACY_KEY",
    "PlaceFacts",
    "PlacePrivacy",
    "is_private_place",
    "said_privacy",
]
