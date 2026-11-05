"""The handle somebody outside the pod is known by, read off a message they sent.

A contact is a handle something trustworthy vouched for: the WhatsApp number in
a payload Meta signed, the Telegram user id in one Telegram did, an email
address the receiving mail service authenticated, a Slack user in an event
Slack signed. Nothing else -- not a display name, not an address typed into a
message -- makes somebody a contact.

Where they wrote decides which handles count:

* **In a private chat** (``private_handle``) only the platforms a stranger can
  reach the pod's own bot on: WhatsApp, Telegram, email.
* **In a group** (``group_handle``) also a Slack sender from the other company
  in a channel shared with it: there, they are the pod's outsiders.
* **A Slack user, by their own workspace** (``workspace_handle``): the name
  for anybody else on Slack -- a colleague in a workspace the pod's
  organization holds, privately or in a group. Nothing here decides that they
  are one (``slack_workspace_people`` does, after the sender is resolved), so
  neither of the two above returns it: the group log, which runs before
  anything knows whether a sender is in the pod, must not open a contact for a
  member it has not recognised yet.

A handle with nothing left once normalised -- a "number" of punctuation --
names nobody, and is dropped rather than opened as a contact.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.email_authentication import (
    EmailAuthenticationVerdict,
)
from app.modules.contacts.contracts import IdentityKind, normalize_handle

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SenderHandle:
    """A sender's handle, normalised, and whether anything vouched for it."""

    kind: IdentityKind
    value: str
    vouched_for: bool


def private_handle(parsed: ParsedInboundSurfaceEvent) -> SenderHandle | None:
    """The handle a private message names its sender by, if it has one."""
    return _normalised(parsed, _chat_or_mail_handle(parsed))


def group_handle(parsed: ParsedInboundSurfaceEvent) -> SenderHandle | None:
    """The handle a group message names its sender by, if it has one."""
    if parsed.platform is SurfacePlatform.SLACK:
        return _normalised(parsed, _slack_outsider_handle(parsed))
    return _normalised(parsed, _chat_or_mail_handle(parsed))


def workspace_handle(parsed: ParsedInboundSurfaceEvent) -> SenderHandle | None:
    """``{their own team}:{their user}`` for any Slack sender Slack names a team for.

    Their own team, never the installing one (``tenant_id``): a person from
    another workspace named under this one would be a second contact for the
    same person ``group_handle`` already knows by their own.
    """
    if parsed.platform is not SurfacePlatform.SLACK:
        return None
    return _normalised(parsed, _slack_handle(parsed))


def _normalised(
    parsed: ParsedInboundSurfaceEvent,
    found: tuple[IdentityKind, str, bool] | None,
) -> SenderHandle | None:
    if found is None:
        return None
    kind, raw, vouched = found
    value = normalize_handle(kind, raw)
    if not value:
        logger.info(
            "agent_surfaces.contacts.handle_unusable.observed",
            platform=parsed.platform.value,
            kind=kind.value,
        )
        return None
    return SenderHandle(kind, value, vouched)


def _chat_or_mail_handle(
    parsed: ParsedInboundSurfaceEvent,
) -> tuple[IdentityKind, str, bool] | None:
    platform = parsed.platform
    if platform is SurfacePlatform.WHATSAPP:
        return _whatsapp_number(parsed)
    if platform is SurfacePlatform.TELEGRAM:
        value = parsed.sender_external_user_id
        return (IdentityKind.TELEGRAM, value, True) if value else None
    if platform.is_email:
        value = parsed.sender_email or parsed.sender_external_user_id
        if not value:
            return None
        vouched = parsed.sender_authentication == EmailAuthenticationVerdict.PASS
        return IdentityKind.EMAIL, value, vouched
    return None


def _whatsapp_number(
    parsed: ParsedInboundSurfaceEvent,
) -> tuple[IdentityKind, str, bool] | None:
    """The sender's number, as Meta names it: international, with no ``+``.

    The ``+`` goes back on before it is read, so a number Meta vouched for is
    never taken for one in the pod's own region -- ``447700900123`` read as a
    US number is somebody else. A group participant WhatsApp names only by a
    business-scoped user id has no number, and so no phone handle: that id is
    not a number, and read as one it named a stranger.
    """
    number = (parsed.sender_phone or "").strip().lstrip("+")
    if not number:
        return None
    return IdentityKind.PHONE, f"+{number}", True


def _slack_outsider_handle(
    parsed: ParsedInboundSurfaceEvent,
) -> tuple[IdentityKind, str, bool] | None:
    """``{their team}:{their user}`` for a sender from the other company.

    Only one Slack marked as another workspace's, in a channel shared with it.
    Somebody in the pod's own workspace is a contact only once resolution has
    said they are not in the pod (``workspace_handle``).
    """
    if not parsed.metadata.get("sender_is_external"):
        return None
    return _slack_handle(parsed)


def _slack_handle(
    parsed: ParsedInboundSurfaceEvent,
) -> tuple[IdentityKind, str, bool] | None:
    # Vouched for: the event that names them is one Slack signed.
    team = parsed.metadata.get("sender_team_id")
    user = parsed.sender_external_user_id
    if not isinstance(team, str) or not team or not user:
        return None
    return IdentityKind.SLACK, f"{team}:{user}", True
