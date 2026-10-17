"""The handle somebody outside the pod is known by, read off a message they sent.

A contact is a handle something trustworthy vouched for: the WhatsApp number in
a payload Meta signed, the Telegram user id in one Telegram did, an email
address the receiving mail service authenticated, a Slack user from another
company in an event Slack signed. Nothing else -- not a display name, not an
address typed into a message -- makes somebody a contact.

Where they wrote decides which handles count:

* **In a private chat** (``private_handle``) only the platforms a stranger can
  reach the pod's own bot on: WhatsApp, Telegram, email. A person writing to a
  workspace bot on Slack or Teams is in that workspace -- the pod's own people.
* **In a group** (``group_handle``) also a Slack sender from the other company
  in a channel shared with it: there, they are the pod's outsiders.

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
        value = parsed.sender_phone or parsed.sender_external_user_id
        return (IdentityKind.PHONE, value, True) if value else None
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


def _slack_outsider_handle(
    parsed: ParsedInboundSurfaceEvent,
) -> tuple[IdentityKind, str, bool] | None:
    """``{their team}:{their user}`` for a sender from the other company.

    Only one Slack marked as another workspace's, in a channel shared with it:
    anybody in the pod's own workspace is a colleague, never a contact.
    """
    metadata = parsed.metadata
    team = metadata.get("sender_team_id")
    user = parsed.sender_external_user_id
    if not metadata.get("sender_is_external") or not isinstance(team, str) or not user:
        return None
    return IdentityKind.SLACK, f"{team}:{user}", True
